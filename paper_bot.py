# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from requests_oauthlib import OAuth1Session
import pandas as pd
from datetime import datetime, timedelta
import slackweb
import json
from google.cloud import storage as gcs
from retry import retry

from config.config import *

# arxivのtagのmappingの読み込み
with open("./config/cs_tag.json", "r") as f_tag:
    TAG_DICT = json.load(f_tag)


class ArxivPop(object):

    def __init__(self, previous_day=7, topn=5):
        self.publish_day = datetime.now() - timedelta(days=previous_day)
        self.df_papers = pd.DataFrame()
        self.topn = topn  # 何位までslackに送るか
        self.list_color = ["#800000", "#008000", "#000080", "#808000", "#800080", "#008080"]
        # self.list_color = ["#008000", "#006020", "#004040", "#002060", "#000080", "#008000"]

    def arxiv_papers(self):
        """
        arxivからのリストを取得

        parameters
        __________
        self.publish_day : date
            The day papers were published

        Returns
        _______
        papers : list
            list of arxiv papers' title and url of abstract page

        """
        publish_day_str = self.publish_day.strftime('%Y-%m-%d')
        publish_day_after_str = (self.publish_day + timedelta(days=1)).strftime('%Y-%m-%d')

        query_arxiv = "https://arxiv.org/search/advanced?advanced=" \
                      + "&terms-0-operator=AND" \
                      + "&terms-0-term=" \
                      + "&terms-0-field=title" \
                      + "&classification-computer_science=y" \
                      + "&classification-physics_archives=all" \
                      + "&date-year=" \
                      + "&date-filter_by=date_range" \
                      + "&date-from_date=" + publish_day_str \
                      + "&date-to_date=" + publish_day_after_str \
                      + "&date-date_type=submitted_date_first" \
                      + "&abstracts=hide" \
                      + "&size=200" \
                      + "&order=-announced_date_first"

        ret_arxiv = requests.get(query_arxiv)
        soup_arxiv = BeautifulSoup(ret_arxiv.text, "html.parser")

        list_title = [title.text.strip() for title in soup_arxiv.find_all('p', class_="title is-5 mathjax")]
        list_url = [url.a.attrs['href'] for url in soup_arxiv.find_all('p', class_="list-title level-left")]
        list_tag = [tag.text for tag in soup_arxiv.find_all(class_="tag is-small search-hit tooltip is-tooltip-right")]

        self.df_papers['title'] = list_title
        self.df_papers['url'] = list_url
        self.df_papers['tag'] = list_tag

        print(len(self.df_papers))

        ################## for debub
        self.df_papers = self.df_papers[:20]
        ##################

        return self.df_papers

    @retry(tries=4, delay=60, backoff=4, max_delay=900)
    def twitter_api(self, twitter_session, search_word):
        """
        twitterのAPIを叩いて、データを取得

        parameters
        __________
        twitter_session : object
            authorized twitter session. keys are already prepared.

        search_word : string
            word for search in twitter.

        Returns
        _______
        reactions : dict
            dict of twitter reactions which include "num_tweet", "total_retweet", "total_favorite"
        """

        url = 'https://api.twitter.com/1.1/search/tweets.json'
        params = {'q': search_word, 'count': '100'}

        req = twitter_session.get(url, params=params)
        req_json = req.json()

        reactions = {}
        len_req = len(req_json['statuses'])
        retweets = [req_json['statuses'][i]['retweet_count'] for i in range(len_req) if
                    ('retweeted_status' not in req_json['statuses'][i])]
        favorites = [req_json['statuses'][i]['favorite_count'] for i in range(len_req) if
                     ('retweeted_status' not in req_json['statuses'][i])]

        reactions['num_tweet'] = len(retweets)
        reactions['total_retweet'] = sum(retweets)
        reactions['total_favorite'] = sum(favorites)

        return reactions

    def twitter_reaction(self):
        """
        twitterのAPIを叩いて、データを取得

        parameters
        __________
        self.df_papers : Dataframe
            papers' title and url is included.

        Returns
        _______
        None

        Updates
        _______
        self.df_papers : Dataframe
            the number of that papers' tweet, retweet, favorite is added.
        """

        twitter_session = OAuth1Session(KEYS_TWITTER['consumer_key'], KEYS_TWITTER['consumer_secret'],
                                        KEYS_TWITTER['access_token'], KEYS_TWITTER['access_secret'])

        df_reactions = pd.DataFrame(columns=['num_tweet', 'total_retweet', 'total_favorite'])

        for url in self.df_papers['url']:
            reactions = self.twitter_api(twitter_session=twitter_session, search_word=url)
            se = pd.Series(reactions)
            df_reactions = df_reactions.append(se, ignore_index=True)

        self.df_papers = pd.concat([self.df_papers, df_reactions], axis=1)

    def sort_reactions(self):
        """
        reactionsを一つの指標にして、並び替える

        parameters
        __________
        self.df_papers : Dataframe
            the papers' information is stored.

        Returns
        _______
        None

        Updates
        _______
        self.df_papers : Dataframe
            "score": total score calculated from twitter and cse results is added.
            And sorted by "score".
        """

        self.df_papers['score'] = self.df_papers['num_tweet'] + \
                                  self.df_papers['total_retweet'] + \
                                  self.df_papers['total_favorite']

        self.df_papers = self.df_papers.sort_values('score', ascending=False).reset_index(drop=True)

    def save_as_csv(self):
        """
        self.df_papersにタイムスタンプを押して、"temp.csv"としてローカルに一度保存。
        ファイル名を"df_arxiv_pop_20181003.csv"として、GCSに送る。

        parameters
        __________
        self.df_papers : Dataframe
            the papers' information is stored.

        Returns
        _______
        None

        Updates
        _______
        self.df_papers : Dataframe
            "timestamp": timestamp is added.
        """

        # timestampをつけて一旦ローカルに保存
        self.df_papers['timestamp'] = [datetime.now()] * len(self.df_papers)
        fname = 'temp.csv'
        self.df_papers.to_csv(fname, index=False)

        # GCSに送る
        client = gcs.Client(PROJECT_NAME)
        bucket = client.get_bucket(BUCKET_NAME)

        today = datetime.now().strftime('%Y%m%d')
        blob_name = 'df/df_arxiv_pop_' + today + '.csv'
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(fname)

    def get_attachment(self, n):
        """
        slackに通知するための、attachmentを修飾していく。

        parameters
        __________
        self.df_papers : Dataframe
            the papers' information is stored.

        Returns
        _______
        attachment : dict
            summary of the each paper's information for notification of slack
        """

        text = "*" + str(self.df_papers['num_tweet'][n]) + "* Tweets  " + \
               "*" + str(self.df_papers['total_retweet'][n]) + "* Retweets  " + \
               "*" + str(self.df_papers['total_favorite'][n]) + "* Favorites "

        attachment = {"title": self.df_papers['title'][n],
                      "title_link": self.df_papers['url'][n],
                      "author_name": self.df_papers['tag'][n] + "  (" + TAG_DICT[self.df_papers['tag'][n]] + ")",
                      "text": text,
                      "mrkdwn_in": ["text"],
                      "color": self.list_color[n]}
        return attachment

    def topn_to_slack(self):
        """
        scoreが上位のarxivを抜き出して、slackに送信する。

        parameters
        __________
        self.df_papers : Dataframe
            the papers' information is stored.

        Returns
        _______
        None
        """
        slack = slackweb.Slack(url=SLACK_URL)
        attachments = []
        for n in range(self.topn):
            attachments.append(self.get_attachment(n))

        text = "     *" + self.publish_day.strftime('%m/%d') + " 発行 話題のarxiv  (全" + str(len(self.df_papers)) + "記事中)*"
        slack.notify(text=text, attachments=attachments)



if __name__ == '__main__':
    arxiv = ArxivPop()
    arxiv.arxiv_papers()
    arxiv.twitter_reaction()
    #print("本日のarxiv数は", len(arxiv.df_papers))
    #print(arxiv.df_papers[:5])

    arxiv.sort_reactions()

    arxiv.save_as_csv()

    arxiv.topn_to_slack()

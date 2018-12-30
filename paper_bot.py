# -*- coding: utf-8 -*-
import requests
from bs4 import BeautifulSoup
from requests_oauthlib import OAuth1Session
import pandas as pd
from datetime import datetime, timedelta
import slackweb
import json
import boto3
from retry import retry
from time import sleep
import os

# Twitterのkeyなどの定数を格納
from setting import Params

# arxivのtagのmappingの読み込み
with open("files/cs_tag.json", "r") as f_tag:
    TAG_DICT = json.load(f_tag)

# 'test' or 'prot'が入る
TEST_OR_PROD = os.environ.get('ARXIV_POP_TEST_OR_PROD')
if TEST_OR_PROD not in ('test','prod'):
    raise ValueError("TEST_OR_PROD have no value or incorrect value")


class ArxivPop(object):

    def __init__(self, previous_day=7, topn=5):
        self.publish_day = datetime.now() - timedelta(days=previous_day)
        self.df_papers = pd.DataFrame()
        self.topn = topn  # 何位までslackに送るか
        self.list_color = ["#800000", "#008000", "#000080", "#808000", "#800080", "#008080"]
        # self.list_color = ["#008000", "#006020", "#004040", "#002060", "#000080", "#008000"]

    def arxiv_papers(self):
        """
        arxivからのリストをAPIを叩いてを取得

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
                      + "&classification-include_cross_list=exclude" \
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
        list_url = [url.a.attrs['href'] for url in soup_arxiv.find_all('p', class_="list-title is-inline-block")]
        list_tag = [tag.text for tag in soup_arxiv.find_all(class_="tag is-small is-link tooltip is-tooltip-top")]

        self.df_papers['title'] = list_title
        self.df_papers['url'] = list_url
        self.df_papers['tag'] = list_tag

        print(len(self.df_papers))

        # testの場合は論文数を絞る
        if TEST_OR_PROD == 'test':
            self.df_papers = self.df_papers[:20]

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

        twitter_session = OAuth1Session(Params.TWITTER_CONSUMER_KEY, Params.TWITTER_CONSUMER_SECRET,
                                        Params.TWITTER_ACCESS_TOKEN, Params.TWITTER_ACCESS_SECRET)

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
        filename_local = 'files/temp.csv'
        self.df_papers.to_csv(filename_local, index=False)

        # s3に送る
        s3 = boto3.resource('s3')
        bucket = s3.Bucket(Params.S3_BUCKET_NAME)

        today = datetime.now().strftime('%Y%m%d')
        filename_in_s3 = 'daily/df_arxiv_pop_' + today + '.csv'

        if TEST_OR_PROD == 'prod':
            bucket.upload_file(filename_local, filename_in_s3)

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

    def topn_to_slack(self, slack_url):
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
        slack = slackweb.Slack(url=slack_url)
        attachments = []
        for n in range(self.topn):
            attachments.append(self.get_attachment(n))

        text = "     *" + self.publish_day.strftime('%m/%d') + " 発行 話題のarxiv  (全" + str(len(self.df_papers)) + "記事中)*"

        if TEST_OR_PROD == 'test':
            text = "[test]\n" + text

        slack.notify(text=text, attachments=attachments)


    def make_tweet(self, n, surplus):
        """
        dfからtweetを作成

        parameters
        __________
        n : int
            rank of Arxiv paper on Twitter
        surplus : int
            the number of character the tweet exceed 280

        Returns
        _______
        tweet : str
            tweet of the Arxiv information
        """

        tweet = f"{self.publish_day.strftime('%Y/%m/%d')} 投稿 {n+1}位\n" + \
                f"{self.df_papers['tag'][n][3:]}({TAG_DICT[self.df_papers['tag'][n]]})\n" + \
                f"{self.df_papers['title'][n][:len(self.df_papers['title'][n])-surplus]}\n" + \
                f"{self.df_papers['url'][n]}\n" + \
                f"{self.df_papers['num_tweet'][n]} Tweets  {self.df_papers['total_retweet'][n]} Retweets  {self.df_papers['total_favorite'][n]} Favorites"

        if TEST_OR_PROD == 'test':
            tweet = "[test]\n" + tweet

        return tweet


    #@retry(tries=4, delay=60, backoff=4, max_delay=900)
    def tweet_to_twitter(self, twitter_session, n):
        """
        twitterのAPIを叩いて、n番目のarxivについてつぶやく

        parameters
        __________
        twitter_session : object
            authorized twitter session. keys are already prepared.

        n : int
            rank of Arxiv paper on Twitter

        Returns
        _______
        None
        """

        surplus = 0
        tweet = self.make_tweet(n, surplus)
        print(len(tweet), n)
        surplus = len(tweet) - 260
        if surplus > 0:
            tweet = self.make_tweet(n, surplus)
            print(len(tweet), n)

        url = "https://api.twitter.com/1.1/statuses/update.json"
        params = {"status": tweet}
        req = twitter_session.post(url, params=params)
        print(req)
        print(req.text)


    def topn_to_twitter(self):
        """
        scoreが上位のarxivを抜き出して、twitterに送信する。

        parameters
        __________
        self.df_papers : Dataframe
            the papers' information is stored.

        Returns
        _______
        None
        """

        twitter_session = OAuth1Session(Params.TWITTER_CONSUMER_KEY, Params.TWITTER_CONSUMER_SECRET,
                                        Params.TWITTER_ACCESS_TOKEN, Params.TWITTER_ACCESS_SECRET)

        for n in range(self.topn)[::-1]:
            self.tweet_to_twitter(twitter_session, n)
            sleep(1)



if __name__ == '__main__':
    arxiv = ArxivPop()
    arxiv.arxiv_papers()
    arxiv.twitter_reaction()

    arxiv.sort_reactions()

    arxiv.save_as_csv()

    arxiv.topn_to_slack(Params.SLACK_URL_PRIVATE)
    if TEST_OR_PROD == 'prod':
        arxiv.topn_to_slack(Params.SLACK_URL_OFFICE)
        arxiv.topn_to_twitter()

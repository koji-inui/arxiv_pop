# arxiv_pop

## 概要
1週間前にarXivに投稿されたComputer Scienceの論文から、Twitterでの反応が大きいもの5件を毎日つぶやくbotです！

Twitterは[**こちら**](https://twitter.com/arxiv_pop)。



Qiitaに記事を書きました。
[**こちら**](https://qiita.com/kj_ini77/items/11057fab98036ca2a3c0)です。


## 環境構成
![arxiv_pop](https://user-images.githubusercontent.com/18414885/47971162-1e3f0280-e0d2-11e8-96ba-222aea08a425.png)


1. DockerイメージをECRから、Twitter等のアクセスキーをParameter Storeから取得

2. 1週間前に投稿されたComputer Scienceの論文を全て取得

3. 論文のURLをTwitterの検索APIに投げて、ツイート数、リツイート数、いいねの数を取得

4. 全部足して、大きいもの順に並び替えて、上位5件を抽出する。

5. ツイッターとSlackに通知する。

6. ログをS3に保存


## 通知先
Twitter
<img width="588" alt="twitter_screenshot" src="https://user-images.githubusercontent.com/18414885/47971179-55adaf00-e0d2-11e8-8cfa-f667b29c1262.png">

slackへの通知
<img width="630" alt="slack_screenshot" src="https://user-images.githubusercontent.com/18414885/47971186-5fcfad80-e0d2-11e8-85c8-6adef662fbdd.png">


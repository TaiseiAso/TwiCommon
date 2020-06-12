# TwiCommon
2020/03/17~ Aso Taisei
Twitterを用いるCS17の日本語研究者のために簡単に使えるスクリプトを
***
## 概要
Twitterから日本語話者による対話をリアルタイムに収集するスクリプト群

## 要件
- python3系
- pyyaml
- tweepy
- MeCab (辞書を mecab-ipadic-NEologd に更新することを推奨)

## 準備
- Twitter API Key を取得し ./config.yml に記述する
- インターネット接続がされていることを確認する
- カレントディレクトリを ./TwiCommon に移動

## 手順
1. Twitterからリアルタイムに対話を取得し形態素解析、正規化処理、フィルタリング処理を施す
    ```
    $ python twitter.py
    ```
    ./data/ にツイートデータ tweet.txt とそれに対応するリプライデータ reply.txt が出力される。また、tweet.txt に対応した品詞データ tweet-part.txt も出力できる。

2. Ctrl+C でリアルタイム収集を中断できる。./data/ に再開用の一時保存ファイル check_point.txt が保存される。削除しても動作に問題はないがデータが重複する可能性がある

## 備考
- 各種設定の変更は ./config.yml を参照してください
- 各出力ファイルが必要なくなった場合は ./data/ にあるファイルを削除するだけで大丈夫です

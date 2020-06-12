# coding: utf-8

"""Twitterから日本語話者による対話をリアルタイムに収集する"""
__author__ = "Aso Taisei"
__date__ = "12 Jun 2020"

import os
import sys
import yaml
import json
import re
import time
import unicodedata
import MeCab
import tweepy
from tweepy import OAuthHandler, Stream
from tweepy.streaming import StreamListener

# 指定した複数のインデックスに対応する要素をリストから削除するラムダ式
dellist = lambda items, indices: [item for idx, item in enumerate(items) if idx not in indices]

# ストリームを再開する際の待機時間
tcpip_delay = 0.25

class QueueListener(StreamListener):
    """Twitterから日本語話者による対話をリアルタイムに収集するクラス"""

    def __init__(self):
        """コンストラクタ"""
        super().__init__()

        # ファイルパス
        self.config_path = "./config.yml"
        self.tweet_path = "./data/tweet.txt"
        self.reply_path = "./data/reply.txt"
        self.tweet_part_path = "./data/tweet-part.txt"
        self.check_point_path = "./data/check_point.txt"

        # 設定
        if not os.path.isfile(self.config_path):
            print("{}: cannot find".format(self.config_path))
            sys.exit(1)

        try:
            config = yaml.load(stream=open(self.config_path, 'r', encoding='utf-8'), Loader=yaml.SafeLoader)
        except:
            print("{}: file read error".format(self.config_path))
            sys.exit(1)

        filter = config['filter']
        self.need = filter['need']
        self.needless = filter['needless']
        self.dump = filter['dump']
        self.length = filter['length']
        self.output = config['output']

        # tweepy関連の情報取得
        cfg_auth = config['twitter_api_key']
        try:
            self.auth = OAuthHandler(cfg_auth['consumer_key'], cfg_auth['consumer_secret'])
            self.auth.set_access_token(cfg_auth['access_token'], cfg_auth['access_token_secret'])
            self.api = tweepy.API(self.auth)
        except:
            print("API access error")
            sys.exit(1)

        # 分かち書きするモジュール
        self.tagger = MeCab.Tagger('-Ochasen')

        # メンバ変数の初期化
        self.tweet_ids = set()  # 発見済みのツイートIDの集合 (重複回避用)
        self.queue = []         # 同時に処理するデータを一時的に蓄積
        self.BATCH_SIZE = 100   # 一度に処理するデータ数
        self.dump_count = 0     # 保存した対話データ数

        # チェックポイントの読み込み
        self.load_check_point()

    # 例外処理 #################################################################
    def on_error(self, status):
        """
        Streamオブジェクトでの処理中にステータスコード200以外が返ってきた際に呼ばれるメソッド
        @param:str status メッセージ
        """
        print('\nON ERROR: ', status)
        self.save_check_point()
        return True

    def on_limit(self, track):
        """
        データが流量オーバーした際に呼ばれるメソッド
        @param:str track メッセージ
        """
        print('\nON LIMIT: ', track)
        self.save_check_point()
        return

    def on_exception(self, exception):
        """
        Streamオブジェクトでの処理中に例外が発生した際に呼ばれるメソッド
        @param:str exception メッセージ
        """
        print('\nON EXCEPTION: ', exception)
        self.save_check_point()
        return

    def on_connect(self):
        """接続した際に呼ばれるメソッド"""
        print('\nON CONNECT')
        return

    def on_disconnect(self, notice):
        """
        Twitter側から切断された際に呼ばれるメソッド
        @param:str notice メッセージ
        """
        print('\nON DISCONNECT: ', notice.code)
        self.save_check_point()
        return

    def on_timeout(self):
        """Streamのコネクションがタイムアウトした際に呼ばれるメソッド"""
        print('\nON TIMEOUT')
        self.save_check_point()
        return True

    def on_warning(self, notice):
        """
        Twitter側からの処理警告がきた際に呼ばれるメソッド
        @param:str notice メッセージ
        """
        print('\nON WARNING: ', notice.message)
        self.save_check_point()
        return
    ############################################################################

    def on_data(self, data):
        """
        処理すべきデータが飛んできた際に呼ばれるメソッド
        @param data json形式のツイートデータ
        @return True: 成功、False: 失敗
        """
        # json形式のデータを読み込む
        raw = json.loads(data)
        return self.on_status(raw)

    def on_status(self, raw):
        """
        ツイートをキューに保存する
        @param raw ツイートデータの構造体
        @return True: 成功、False: 失敗
        """
        if isinstance(raw.get('in_reply_to_status_id'), int):
            tweet = [raw['in_reply_to_status_id'], raw['user']['id'], self.del_username(unicodedata.normalize('NFKC', raw['text'])), raw['id']]

            if self.check(tweet[2]):
                tweet[2], parts = self.parse(self.normalize(tweet[2]))
                # フィルタリング
                if self.need_check(parts) and self.needless_check(parts):
                    tweet[2], parts = self.dump_filter(tweet[2], parts)
                    if self.length_check(tweet[2]):
                        self.queue.append([[tweet, parts]])
                        self.tweet_ids.add(tweet[3])

                        while len(self.queue) == self.BATCH_SIZE:
                            if not self.lookup():
                                return False
        return True

    def lookup(self):
        """
        キューにあるツイートに対応するリプライ先を一斉に一度だけ取得
        リプライ先がそれ以上存在しないデータはファイルに保存
        @return True: 成功、False: 失敗
        """
        ids = []
        for dialogs in self.queue:
            ids.append(dialogs[-1][0][0])

        replys = self.api.statuses_lookup(ids)
        replys_dic = {reply.id_str: [reply.in_reply_to_status_id, reply.user.id, self.del_username(unicodedata.normalize('NFKC', reply.text)), reply.id] for reply in replys}

        dump_idxs = []

        for idx in range(self.BATCH_SIZE):
            interrupte = False
            tweet = replys_dic.get(str(ids[idx]))

            # リプライ先が本当に存在しているかを確認
            interrupte = True
            if tweet:
                # 使用データの候補として適切かを判定
                if self.check(tweet[2]) and self.talker_check(tweet[1], idx):
                    tweet[2], parts = self.parse(self.normalize(tweet[2]))
                    # フィルタリング
                    if self.need_check(parts) and self.needless_check(parts):
                        tweet[2], parts = self.dump_filter(tweet[2], parts)
                        if self.length_check(tweet[2]):
                            self.queue[idx].append([tweet, parts])
                            if isinstance(tweet[0], int) and tweet[3] not in self.tweet_ids:
                                interrupte = False
                            self.tweet_ids.add(tweet[3])

            # リプライ探索中断して正常な部分のみを保存
            if interrupte:
                dump_idxs.append(idx)
                if len(self.queue[idx]) >= 2:
                    if not self.dump_data(self.queue[idx]):
                        return False

        self.queue = dellist(self.queue, dump_idxs)
        return True

    def dump_data(self, dialog):
        """
        対話データをファイルに保存する
        @param dialog 保存する対話データ
        @return True: 成功、False: 失敗
        """
        # 時系列順に並べ替え
        dialog.reverse()

        # ツイート内容だけのリストを作成
        tweets = [[tweet[0][2], tweet[1]] for tweet in dialog]

        with open(self.tweet_path, 'a', encoding='utf-8') as f_in,\
        open(self.reply_path, 'a', encoding='utf-8') as f_tar:
            for i in range(len(tweets)-1):
                f_in.write(' '.join(tweets[i][0]) + "\n")
                f_tar.write(' '.join(tweets[i+1][0]) + "\n")

        if self.output['part']:
            with open(self.tweet_part_path, 'a', encoding='utf-8') as f:
                for i in range(len(tweets)-1):
                    f.write(' '.join(tweets[i][1]) + "\n")

        self.dump_count += len(tweets)-1
        print(self.dump_count, " ", end="", flush=True)
        return True

    def del_username(self, text):
        """
        ツイートデータのテキストからユーザ名を除去
        @param text ツイートデータのテキスト
        @return ユーザ名を除去したツイートデータのテキスト
        """
        return re.sub("(^|\s)(@|＠)(\w+)", "", text)

    def check(self, text):
        """
        ツイートデータのテキストに不適切な情報が含まれていないかを判定
        @param text ツイートデータのテキスト
        @return True: 適切、False: 不適切
        """
        # URLを含む（画像も含む）
        if re.compile("((ftp|http|https):\/\/(\w+:{0,1}\w*@)?(\S+)(:[0-9]+)?(\/|\/([\w#!:.?+=&amp;%@!&#45;\/]))?)").search(text):
            return False
        # ハッシュタグを含む
        if re.compile("(?:^|[^ーー゛゜々ヾヽぁ-ヶ一-龠a-zA-Z0-9&_/>]+)[#＃]([ー゛゜々ヾヽぁ-ヶ一-龠a-zA-Z0-9_]*[ー゛゜々ヾヽぁ-ヶ一-龠a-zA-Z]+[ー゛゜々ヾヽぁ-ヶ一-龠a-zA-Z0-9_]*)").search(text):
            return False
        # 英数字を含む
        if re.compile("[a-zA-Z0-9]").search(text):
            return False
        return True

    def talker_check(self, talker, idx):
        """
        二話者の交互発話による対話であるかを判定
        @param talker 話者ID
        @param idx キュー内の対話ID
        @return True: 適切、False: 不適切
        """
        return talker != self.queue[idx][-1][0][1] and (len(self.queue[idx]) == 1 or talker == self.queue[idx][-2][0][1])

    def normalize(self, line):
        """
        文に正規化処理を施す
        @param:str line 正規化する文
        @return:str 正規化された文
        """
        line = re.sub("[\(\[][^笑泣汗]*?[\)\]]", " ", line)
        line = re.sub("[^ぁ-んァ-ヶ一-龠々ー～〜、。!?,.]", " ", line)

        line = re.sub(",", "、", line)
        line = re.sub("\.", "。", line)
        line = re.sub("〜", "～", line)
        line = re.sub("、(\s*、)+|。(\s*。)+", "...", line)

        line = re.sub("!+", "！", line)
        line = re.sub("！(\s*！)+", "！", line)
        line = re.sub("\?+", "？", line)
        line = re.sub("？(\s*？)+", "？", line)

        line = re.sub("～(\s*～)+", "～", line)
        line = re.sub("ー(\s*ー)+", "ー", line)
        line = re.sub("っ(\s*っ)+", "っ", line)
        line = re.sub("ッ(\s*ッ)+", "ッ", line)
        line = re.sub("笑(\s*笑)+", "笑", line)
        line = re.sub("泣(\s*泣)+", "泣", line)
        line = re.sub("汗(\s*汗)+", "汗", line)

        line += "。"
        line = re.sub("[、。](\s*[、。])+", "。", line)

        line = re.sub("[。、！](\s*[。、！])+", "！", line)
        line = re.sub("[。、？](\s*[。、？])+", "？", line)
        line = re.sub("((！\s*)+？|(？\s*)+！)(\s*[！？])*", "!?", line)

        line = re.sub("、\s*([笑泣汗])\s*。", " \\1。", line)
        line = re.sub("(。|！|？|!\?)\s*([笑泣汗])\s*。", " \\2\\1", line)

        line = re.sub("、", " 、 ", line)
        line = re.sub("。", " 。\n", line)

        line = re.sub("(\.\s*)+", " ... ", line)
        line = re.sub("！", " ！\n", line)
        line = re.sub("？", " ？\n", line)
        line = re.sub("!\?", " !?\n", line)

        line = re.sub("\n(\s*[～ー])+", "\n", line)

        line = re.sub("^([\s\n]*[。、！？!?ー～]+)+", "", line)
        line = re.sub("(.+?)\\1{3,}", "\\1\\1\\1", line)

        return line

    def parse(self, line):
        """
        文を形態素解析してノイズ除去する
        @param:str line 形態素解析する文
        @return:list[str] 形態素解析してノイズ除去された文
        @return:list[str] 対応する品詞
        """
        words, parts = [], []

        node = self.tagger.parseToNode(line)
        while node:
            feature = node.feature.split(',')
            if feature[0] == "BOS/EOS" or node.feature in [".", "..", "!", "?"]:
                node = node.next
                continue

            if feature[0] == "名詞":
                if feature[1] in ["一般", "固有名詞", "サ変接続", "形容動詞語幹"]:
                    token = "noun_main"
                elif feature[1] == "代名詞":
                    token = "pronoun"
                else:
                    token = "noun_sub"
            elif feature[0] == "動詞":
                if feature[1] == "自立":
                    token = "verb_main"
                else:
                    token = "verb_sub"
            elif feature[0] == "形容詞":
                if feature[1] == "自立":
                    token = "adjective_main"
                else:
                    token = "adjective_sub"
            elif feature[0] == "副詞":
                token = "adverb"
            elif feature[0] == "助詞":
                token = "particle"
            elif feature[0] == "助動詞":
                token = "auxiliary_verb"
            elif feature[0] == "接続詞":
                token = "conjunction"
            elif feature[0] == "接頭詞":
                token = "prefix"
            elif feature[0] == "フィラー":
                token = "filler"
            elif feature[0] == "感動詞":
                token = "impression_verb"
            elif feature[0] == "連体詞":
                token = "pre_noun"
            elif node.surface == "...":
                token = "three_dots"
            elif node.surface in ["。", "！", "？", "!?"]:
                token = "phrase_point"
            elif node.surface == "、":
                token = "reading_point"
            else:
                token = "other"

            words.append(node.surface)
            parts.append(token)
            node = node.next

        return words, parts

    def need_check(self, parts):
        """
        文が need filter を満たすかを判定する
        @param:list[str] parts 品詞列
        @return:bool 文が need filter を満たすかどうか
        """
        for part in self.need:
            if self.need[part] and part not in parts:
                return False
        return True

    def needless_check(self, parts):
        """
        文が needless filter を満たすかを判定する
        @param:list[str] parts 品詞列
        @return:bool 文が needless filter を満たすかどうか
        """
        for part in parts:
            if self.needless.get(part):
                return False
        return True

    def dump_filter(self, words, parts):
        """
        文の dump filter を満たす品詞の単語のみを抽出する
        @param:list[str] words 単語列
        @param:list[str] parts 対応する品詞列
        @return:list[str] 文の dump filter を満たす品詞の単語のみを抽出した文
        """
        dump_words, dump_parts = [], []
        for word, part in zip(words, parts):
            if self.dump.get(part):
                dump_words.append(word)
                dump_parts.append(part)
        return dump_words, dump_parts

    def length_check(self, words):
        """
        文が length filter を満たすかを判定する
        @param:list[str] words 単語列
        @return:bool 文が length filter を満たすかどうか
        """
        return self.length['min'] <= len(words) <= self.length['max']

    def save_check_point(self):
        '''確認済みのツイートID集合を一時保存する'''
        try:
            f = open(self.check_point_path, 'w', encoding='utf-8')
        except:
            print("{}: file write error".format(self.check_point_path))
        else:
            f.write(str(self.dump_count) + "\n")
            f.write(' '.join(map(str, self.tweet_ids)))
            f.close()

    def load_check_point(self):
        '''前回までに確認済みのツイートID集合があれば取得する'''
        self.tweet_ids.clear()
        self.dump_count = 0
        if os.path.isfile(self.check_point_path):
            try:
                f = open(self.check_point_path, 'r', encoding='utf-8')
            except:
                print("{}: file read error".format(self.check_point_path))
            else:
                self.dump_count = int(f.readline().strip())
                self.tweet_ids = set(map(int, f.readline().strip().split()))
                f.close()

def twitter():
    """Twitterから対話コーパスを作成"""
    while True:
        try:
            listener = QueueListener()
            stream = Stream(listener.auth, listener)
            stream.filter(languages=["ja"], track=['。', '，', '！', '.', '!', ',', '?', '？', '、', '私', '俺', '(', ')', '君', 'あなた'])
        except KeyboardInterrupt:
            listener.save_check_point()
            stream.disconnect()
            break
        except:
            global tcpip_delay
            time.sleep(tcpip_delay)
            tcpip_delay += 0.25
            tcpip_delay = min(tcpip_delay, 16)

if __name__ == '__main__':
    twitter()

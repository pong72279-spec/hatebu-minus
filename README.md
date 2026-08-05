# Hatebu Minus

はてなブックマークの人気・新着エントリーから、見たくないドメインを差し引いて読むための個人用ニュースサイトです。データベースや常時稼働サーバーを使わず、Python、JSON、素のHTML/CSS/JavaScript、GitHub Actions、GitHub Pagesだけで動きます。

初期設定では `togetter.com`、`posfie.com`、`min.togetter.com` と、それらのサブドメインを除外します。

## 主な機能

- 総合、一般、世の中、政治と経済、暮らし、学び、テクノロジー、おもしろ、エンタメ、アニメとゲームを切り替え
- 人気順／新着順を切り替え
- 最低ブックマーク数、ドメイン、タイトルで絞り込み
- URL正規化と重複除去
- 同一ドメインが3件以上連続しないように並び替え
- ライト／ダークモード（初期状態はOS設定に追従）
- スマートフォン1カラム、PC最大幅のレスポンシブ表示
- 1時間ごとの自動取得と手動実行
- 全取得失敗時は前回の `data/entries.json` を維持

## 構成

```text
.
├── .github/workflows/update-and-deploy.yml  # 定期取得・テスト・Pages公開
├── assets/
│   ├── app.js                               # 絞り込み・描画・テーマ切替
│   └── styles.css                           # レスポンシブUI
├── data/entries.json                        # 取得済み記事（自動更新）
├── scripts/
│   ├── build_site.py                        # dist/ の生成とJSON検証
│   └── fetch_entries.py                     # RSS取得・正規化・統合
├── tests/test_fetch_entries.py              # 最低限のユニットテスト
├── config.json                              # 除外・保持設定
├── index.html
└── README.md
```

## ローカルで動かす

Python 3.11以上が必要です。外部Pythonパッケージは使いません。

```bash
python3 -m unittest discover -s tests -v
python3 scripts/fetch_entries.py
python3 scripts/build_site.py
python3 -m http.server 8000 -d dist
```

ブラウザで `http://localhost:8000/` を開いてください。RSS取得を省略した場合は0件表示の確認ができます。`index.html` を直接開くとブラウザの制約でJSONを取得できないため、必ずローカルHTTPサーバーを使います。

macOSで `python3` 実行時にXcodeライセンス未同意のエラーが出る場合は、表示された案内に従ってXcodeライセンスを確認・同意するか、Python公式版など別のPython 3.11以上を使用してください。GitHub Actions上の実行には影響しません。

## 設定を変える

ルートの `config.json` を編集します。

```json
{
  "blockedDomains": [
    "togetter.com",
    "posfie.com",
    "min.togetter.com"
  ],
  "blockedKeywords": [],
  "minimumBookmarkCount": 3,
  "retentionDays": 7
}
```

- `blockedDomains`: 除外するドメイン。`example.com` を指定すると `www.example.com` などのサブドメインも除外します。
- `blockedKeywords`: タイトルまたは概要に含まれると除外する語句。大文字・小文字は区別しません。
- `minimumBookmarkCount`: 取得データへ保存する最低ブックマーク数。画面の入力欄の初期値にもなります。
- `retentionDays`: 一度取得した記事を保持する日数。1〜90を指定できます。

変更後は手元で `python3 scripts/fetch_entries.py` を実行するか、GitHubへpushしてActionsを手動実行してください。静的サイトを軽量に保つため、保持件数には安全上の上限5,000件があります。

## データ取得の仕様

各カテゴリーについて、はてなブックマークの公開RSSの「人気」と「新着」を1本ずつ、合計20本取得します。全リクエストは直列で、各リクエスト間に0.4秒の間隔を置きます。タイムアウトは15秒、再試行は1回、User-Agentは `Hatebu-Minus/1.0` です。個別記事ページへの追加アクセスは行いません。

主な取得元の例:

- 人気・総合: `https://b.hatena.ne.jp/hotentry.rss`
- 人気・テクノロジー: `https://b.hatena.ne.jp/hotentry/it.rss`
- 新着・総合: `https://b.hatena.ne.jp/entrylist.rss?sort=recent`
- 新着・テクノロジー: `https://b.hatena.ne.jp/entrylist/it.rss?sort=recent`

RSS内のHTMLはそのまま保存・表示せず、概要をプレーンテキスト化します。フロントエンドも `innerHTML` を使わず、記事タイトルや概要を `textContent` で描画します。URLはHTTP/HTTPSだけを許可します。

更新は一時ファイルへの書き込みが完了してから置換するため、途中で停止しても既存JSONを壊しません。20本すべての取得に失敗した場合、スクリプトは失敗終了し、既存JSONを一切変更しません。一部だけ失敗した場合は成功分を統合し、保持期間内の前回データを残します。

## GitHub Pagesで公開する

### 1. GitHubに新規リポジトリを作る

GitHub上で `hatebu-minus` という空のPublicリポジトリを作成します。READMEや `.gitignore` はGitHub側では追加しないでください。その後、このディレクトリで次を実行します（`YOUR_NAME` はGitHubユーザー名に置き換えます）。

```bash
git add .
git commit -m "Initial Hatebu Minus site"
git remote add origin https://github.com/YOUR_NAME/hatebu-minus.git
git push -u origin main
```

GitHub CLIを使う場合は、代わりに次でも公開できます。

```bash
gh repo create hatebu-minus --public --source=. --remote=origin --push
```

### 2. Pagesの公開元をGitHub Actionsにする

GitHubのリポジトリ画面で **Settings → Pages → Build and deployment → Source** を開き、**GitHub Actions** を選びます。

### 3. 初回更新を実行する

**Actions → Update data and deploy Pages → Run workflow** を押します。完了後、Actionsのデプロイ画面またはSettings → Pagesに公開URLが表示されます。

以降は `37 * * * *`（UTC）のスケジュールで1時間ごとに実行されます。GitHub Actionsのスケジュールは混雑時に多少遅れたり、まれに実行が飛ぶ場合があります。ブラウザ側ではJSON取得時にキャッシュ回避パラメータを付け、最新の公開データを確認します。手動実行にも常時対応しています。

## GitHub Actionsの権限

ワークフローには以下を設定済みです。

- `contents: write`: 成功した最新JSONをリポジトリへ保存
- `pages: write`: Pagesへデプロイ
- `id-token: write`: Pagesデプロイ元を検証

組織リポジトリのポリシーやブランチ保護でActionsからのpushが禁止されている場合、JSONの永続化だけを警告付きでスキップし、今回取得したデータのPages公開は続行します。次回の全取得失敗時にも前回JSONを確実に使いたい場合は、ブランチルールでGitHub Actionsからのpushを許可してください。

## 利用上の注意

Hatebu Minusは、はてなブックマーク公式サイトではありません。公開RSSの仕様やカテゴリーURLが変更された場合は `scripts/fetch_entries.py` の `CATEGORIES` と `build_feed_specs()` を更新してください。

参考資料:

- [はてなブックマーク — カテゴリーでみる](https://b.hatena.ne.jp/help/entry/category)
- [GitHub Docs — Using custom workflows with GitHub Pages](https://docs.github.com/en/pages/getting-started-with-github-pages/using-custom-workflows-with-github-pages)
- [GitHub Docs — Configuring a publishing source for your GitHub Pages site](https://docs.github.com/en/pages/getting-started-with-github-pages/configuring-a-publishing-source-for-your-github-pages-site)

## ライセンス

[MIT License](LICENSE)

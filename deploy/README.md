# Deploy: staging + prod (msk01)

## URLs

| Env | Host | Port | Limits |
|-----|------|------|--------|
| **staging** | `https://cat-staging.chatbook.pro` | 8021 | `FREE_LIMIT=999`, `SOCRATIC_MAX_STEPS=0` (полная версия для тестов) |
| **prod** | `https://cat.chatbook.pro` | 8020 | `FREE_LIMIT=5`, `SOCRATIC_MAX_STEPS=3` |

Webhook path: `/webhook` (и UI `/test`).

## Как Лана деплоит сама

1. Правит код в GitHub → push в `master` → **GitHub Actions: Deploy staging** (smoke + выкладка + healthcheck + автооткат).
2. В черновике Яндекс Диалогов временно указывает webhook `https://cat-staging.chatbook.pro/webhook`.
3. Когда staging ок — тег `prod-YYYYMMDD` **или** Actions → Deploy prod → confirm `PROMOTE` (GitHub Environment `production`).

Прод **не** обновляется от обычного push.

## Секреты в GitHub (Settings → Secrets)

- `MSK01_HOST` — например `139.100.238.243` или hostname
- `MSK01_KOT_DEPLOY_USER` — `kot-deploy`
- `MSK01_KOT_DEPLOY_KEY` — private key пользователя `kot-deploy` (без пароля)

На сервере: `/opt/alice-skill-kot-uchenyi/{prod,staging}.env` — не в git.

## Откат вручную

```bash
ssh msk01
ls -lt /opt/alice-skill-kot-uchenyi/releases | head
ln -sfn /opt/alice-skill-kot-uchenyi/releases/<good-sha> /opt/alice-skill-kot-uchenyi/staging  # или prod
systemctl restart cat-kot-uchenyi-staging   # или cat-kot-uchenyi
```

`deploy.sh` сам откатывает symlink, если healthcheck (`ping` → `pong`) не прошёл.

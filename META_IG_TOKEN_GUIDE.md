# META / Instagram Graph API Token Guide

## 1) Чек-лист в Meta
- Приложение: подключён продукт **Instagram Graph API**.
- Приложение: подключён **Facebook Login** (или Facebook Login for Business).
- Вы добавлены в роли (Admin/Developer/Tester) приложения.
- Instagram-аккаунт переведён в **Professional** (Business/Creator).
- IG привязан к Facebook Page **“Kun Hikmati / Мудрость Дня”**.
- В Business Suite у Page видно подключённый IG.

## 2) Шаги в Graph API Explorer (ручные, кратко)
1. В Explorer выбрать нужное приложение.
2. Запросить **User Token** с правами: `pages_show_list`, `pages_read_engagement`, `instagram_basic`.  
   Для постинга/комментов: также `instagram_manage_comments`, `instagram_manage_messages`, `pages_manage_metadata`, `pages_read_user_content`.
3. Выполнить:
   ```
   GET /me/accounts?fields=id,name,access_token,instagram_business_account
   ```
   → берём `page_id`, `page_access_token`, `ig_id` = `instagram_business_account.id`.

Важно:
- `/me/accounts` работает только с **User Token**.  
- С Page Token вернётся ошибка `"nonexistent field accounts"`.  
- Проверку Page Token делаем через `GET /{page_id}?fields=id,name` (а не `/me`).

## 3) curl/скрипт (обмен на long-lived)
Обмен short-lived User → long-lived User:
```
GET https://graph.facebook.com/v24.0/oauth/access_token\
  ?grant_type=fb_exchange_token\
  &client_id={APP_ID}\
  &client_secret={APP_SECRET}\
  &fb_exchange_token={SHORT_USER_TOKEN}
```
После получения LONG_USER_TOKEN снова вызываем:
```
GET /me/accounts?fields=id,name,access_token,instagram_business_account
```
уже с LONG_USER_TOKEN → получаем LONG PAGE TOKEN.

Проверка токена:
```
GET /debug_token?input_token={TOKEN}&access_token={APP_ID}|{APP_SECRET}
```

Тест IG запрос:
```
GET https://graph.facebook.com/v24.0/{ig_id}?fields=id,username,account_type&access_token={PAGE_TOKEN}
```

## 4) Диагностика: IG не находится
- `instagram_business_account` пусто/null → IG не привязан к Page, либо IG не Professional, либо не выданы нужные permissions.  
- Проверьте в Instagram: Настройки → Аккаунт → Профессиональный → Подключение к Странице.  
- Проверьте в Business Suite: Settings → Linked Accounts → Instagram.

## 5) Диагностика: `/me` ломается на Page Token
- Используйте `GET /{page_id}?fields=id,name` для проверки Page Token.  
- `/me` на Page Token может давать "Object does not exist", если токен не user-level или не хватает прав; обращение по конкретному `page_id` точнее.

## 6) Скрипт
Смотрите `scripts/meta_tokens.py` — читает `.env` (APP_ID, APP_SECRET, SHORT_USER_TOKEN), выводит LONG_USER_TOKEN, Page токены и ig_id, плюс готовый тестовый GET. Не храните токены в репозитории.



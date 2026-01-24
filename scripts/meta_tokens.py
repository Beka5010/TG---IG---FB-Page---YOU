import os
import sys
import requests


DEFAULT_VERSION = os.getenv("IG_GRAPH_VERSION", "v24.0").strip() or "v24.0"


def env(name, default=None, required=False):
    v = os.getenv(name, default)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            print(f"Missing env var: {name}", file=sys.stderr)
            sys.exit(1)
        return default
    return v.strip() if isinstance(v, str) else v


def exchange_long_user_token(app_id, app_secret, short_token):
    url = f"https://graph.facebook.com/{DEFAULT_VERSION}/oauth/access_token"
    params = {
        "grant_type": "fb_exchange_token",
        "client_id": app_id,
        "client_secret": app_secret,
        "fb_exchange_token": short_token,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    return data.get("access_token")


def debug_token(token, app_id, app_secret):
    url = f"https://graph.facebook.com/{DEFAULT_VERSION}/debug_token"
    params = {
        "input_token": token,
        "access_token": f"{app_id}|{app_secret}",
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json()


def get_pages(long_user_token):
    url = f"https://graph.facebook.com/{DEFAULT_VERSION}/me/accounts"
    params = {
        "fields": "id,name,access_token,instagram_business_account",
        "access_token": long_user_token,
    }
    r = requests.get(url, params=params, timeout=15)
    r.raise_for_status()
    return r.json().get("data", [])


def main():
    app_id = env("APP_ID", required=True)
    app_secret = env("APP_SECRET", required=True)
    short_user_token = env("SHORT_USER_TOKEN", required=True)

    print("== Exchange short-lived -> long-lived USER token ==")
    long_user_token = exchange_long_user_token(app_id, app_secret, short_user_token)
    print(f"LONG_USER_TOKEN={long_user_token}\n")

    print("== Debug long-lived USER token ==")
    dbg = debug_token(long_user_token, app_id, app_secret)
    print(dbg, "\n")

    print("== Get pages (/me/accounts) with LONG USER token ==")
    pages = get_pages(long_user_token)
    if not pages:
        print("No pages returned. Check permissions: pages_show_list, pages_read_engagement, instagram_basic.")
        sys.exit(0)

    for p in pages:
        page_id = p.get("id")
        page_name = p.get("name")
        page_token = p.get("access_token")
        ig_obj = p.get("instagram_business_account") or {}
        ig_id = ig_obj.get("id")

        print(f"Page: {page_name} ({page_id})")
        print(f"PAGE_TOKEN={page_token}")
        print(f"IG_ID={ig_id}")
        print(f"Check IG: GET https://graph.facebook.com/{DEFAULT_VERSION}/{ig_id}?fields=id,username,account_type&access_token={{PAGE_TOKEN}}\n")

    print("== Token check sample ==")
    print(f"GET https://graph.facebook.com/{DEFAULT_VERSION}/debug_token?input_token={{TOKEN}}&access_token={app_id}|{app_secret}")


if __name__ == "__main__":
    main()



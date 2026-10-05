import os, django
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass
django.setup()

from posts.models import SocialAccount, Platform

fb = Platform.objects.filter(code="facebook").first()
if not fb:
    print("Facebook platform NOT in DB — run seeder.py first")
else:
    print(f"Platform: {fb.name} (code={fb.code})")
    accounts = SocialAccount.objects.filter(platform=fb).select_related("platform")
    print(f"Total Facebook accounts: {accounts.count()}")
    for a in accounts:
        print(f"\n  Label    : {a.account_label}")
        print(f"  Page ID  : {a.external_account_id}")
        print(f"  Status   : {a.status}")
        try:
            cred = a.credential
            print(f"  Token    : {'YES (encrypted)' if cred.access_token else 'MISSING'}")
            print(f"  Expires  : {cred.expires_at}")
            print(f"  Scope    : {cred.scope}")
        except Exception as e:
            print(f"  Credential: MISSING — {e}")

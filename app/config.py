import os

# To generate a new secret key:
# >>> import random, string
# >>> "".join([random.choice(string.printable) for _ in range(24)])
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-on-raspberry")

D_APP_ID = int(os.getenv("D_APP_ID", "1200420960103822"))
import os

SECRET_KEY = "aabbcc"

# Advertise the client-facing ingress, not an internal Compose hostname.
CONTENT_ORIGIN = os.environ.get("PULP_CONTENT_ORIGIN", "http://localhost:8080").rstrip("/")
DATABASES = {"default": {"HOST": "postgres", "ENGINE": "django.db.backends.postgresql", "NAME": "pulp", "USER": "pulp", "PASSWORD": "password", "PORT": "5432", "CONN_MAX_AGE": 0, "OPTIONS": {"sslmode": "prefer"}}}
CACHE_ENABLED = True
REDIS_HOST = "redis"
REDIS_PORT = 6379
REDIS_PASSWORD = ""
ANSIBLE_API_HOSTNAME = "http://pulp_api:24817"
ANSIBLE_CONTENT_HOSTNAME = "http://pulp_content:24816/pulp/content"
ALLOWED_IMPORT_PATHS = ["/tmp"]
ALLOWED_EXPORT_PATHS = ["/tmp"]
TOKEN_SERVER = os.environ.get("PULP_TOKEN_SERVER", CONTENT_ORIGIN + "/token/")
TOKEN_AUTH_DISABLED = False
TOKEN_SIGNATURE_ALGORITHM = "ES256"
PUBLIC_KEY_PATH = "/etc/pulp/certs/token_public_key.pem"
PRIVATE_KEY_PATH = "/etc/pulp/certs/token_private_key.pem"
ANALYTICS = False
STATIC_ROOT = "/var/lib/operator/static/"

# Keep working files on the shared local volume; store content in RustFS/S3.
_storage_backend = os.environ.get("PULP_STORAGE_BACKEND", "filesystem")
if _storage_backend == "rustfs":
    MEDIA_ROOT = ""
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "endpoint_url": os.environ.get("PULP_S3_ENDPOINT_URL", "http://rustfs:9000"),
                "bucket_name": os.environ.get("PULP_S3_BUCKET", "pulp"),
                "region_name": os.environ.get("PULP_S3_REGION", "us-east-1"),
                "access_key": os.environ["PULP_S3_ACCESS_KEY"],
                "secret_key": os.environ["PULP_S3_SECRET_KEY"],
                "signature_version": "s3v4",
                "addressing_style": "path",
                "default_acl": None,
                "file_overwrite": True,
            },
        },
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    # Keep private storage behind Pulp instead of exposing Docker-only URLs.
    REDIRECT_TO_OBJECT_STORAGE = False
elif _storage_backend != "filesystem":
    raise RuntimeError("PULP_STORAGE_BACKEND must be rustfs or filesystem")

import os
import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

S3_ENDPOINT = os.getenv("S3_ENDPOINT", "http://minio:9000")
S3_PUBLIC_URL = os.getenv("S3_PUBLIC_URL", "http://localhost:9020").rstrip("/")
S3_ACCESS_KEY = os.getenv("S3_ACCESS_KEY", "minioadmin")
S3_SECRET_KEY = os.getenv("S3_SECRET_KEY", "minioadmin123")

CATALOG_BUCKET = "catalog-images"
UPLOADS_BUCKET = "client-uploads"


def get_s3_client(endpoint_url: str | None = None):
    """Return a configured boto3 S3 client for MinIO."""
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url or S3_ENDPOINT,
        aws_access_key_id=S3_ACCESS_KEY,
        aws_secret_access_key=S3_SECRET_KEY,
        config=Config(signature_version="s3v4"),
        region_name="us-east-1",
    )


def upload_bytes(
    key: str,
    data: bytes,
    bucket: str = UPLOADS_BUCKET,
    content_type: str = "application/octet-stream",
) -> str:
    """Upload raw bytes to a MinIO bucket and return the object key."""
    client = get_s3_client()
    client.put_object(
        Bucket=bucket,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    return key


def download_bytes(key: str, bucket: str = UPLOADS_BUCKET) -> bytes:
    """Download an object from MinIO and return its raw bytes."""
    client = get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def object_exists(key: str, bucket: str = CATALOG_BUCKET) -> bool:
    """Check whether an object exists in the given MinIO bucket."""
    client = get_s3_client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def get_public_url(key: str, bucket: str = CATALOG_BUCKET) -> str:
    """Return the public HTTP URL for an object in a public-read bucket."""
    return f"{S3_PUBLIC_URL}/{bucket}/{key.lstrip('/')}"


def get_presigned_url(
    key: str,
    bucket: str = UPLOADS_BUCKET,
    expires_in: int = 3600,
) -> str:
    """Generate a temporary presigned URL reachable from the client browser."""
    client = get_s3_client(endpoint_url=S3_PUBLIC_URL)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": key},
        ExpiresIn=expires_in,
    )

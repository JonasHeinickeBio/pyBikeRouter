"""A tiny in-memory stand-in for a boto3 S3 client (the calls S3ArtifactStore makes)."""

from datetime import UTC, datetime

from botocore.exceptions import ClientError


def _client_error(code: str, status: int, operation: str) -> ClientError:
    return ClientError(
        {"Error": {"Code": code, "Message": code}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


class _Body:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


class FakeS3Client:
    def __init__(self, *, bucket: str = "bucket", page_size: int = 2) -> None:
        self.bucket = bucket
        self.page_size = page_size
        self.objects: dict[str, tuple[bytes, datetime, str]] = {}
        self.calls: list[str] = []
        self.bucket_exists = True
        self.fail_delete: set[str] = set()

    def _check(self, kwargs: dict, operation: str) -> None:
        self.calls.append(operation)
        if kwargs.get("Bucket") != self.bucket or not self.bucket_exists:
            raise _client_error("NoSuchBucket", 404, operation)

    def put_object(self, **kwargs):
        self._check(kwargs, "put_object")
        self.objects[kwargs["Key"]] = (
            kwargs["Body"],
            kwargs.get("_modified") or datetime.now(UTC),
            kwargs.get("ContentType", ""),
        )

    def get_object(self, **kwargs):
        self._check(kwargs, "get_object")
        if kwargs["Key"] not in self.objects:
            raise _client_error("NoSuchKey", 404, "GetObject")
        return {"Body": _Body(self.objects[kwargs["Key"]][0])}

    def head_object(self, **kwargs):
        self._check(kwargs, "head_object")
        if kwargs["Key"] not in self.objects:
            raise _client_error("404", 404, "HeadObject")
        return {}

    def delete_object(self, **kwargs):
        self._check(kwargs, "delete_object")
        if kwargs["Key"] in self.fail_delete:
            raise _client_error("InternalError", 500, "DeleteObject")
        self.objects.pop(kwargs["Key"], None)

    def head_bucket(self, **kwargs):
        self._check(kwargs, "head_bucket")

    def generate_presigned_url(self, operation, Params, ExpiresIn):  # noqa: N803 - boto3 names
        return f"https://s3.example/{Params['Bucket']}/{Params['Key']}?expires={ExpiresIn}"

    def get_paginator(self, operation):
        assert operation == "list_objects_v2"
        client = self

        class _Paginator:
            def paginate(self, Bucket, Prefix=""):  # noqa: N803
                client._check({"Bucket": Bucket}, "list_objects_v2")
                keys = sorted(k for k in client.objects if k.startswith(Prefix))
                for i in range(0, max(len(keys), 1), client.page_size):
                    chunk = keys[i : i + client.page_size]
                    yield {
                        "Contents": [
                            {
                                "Key": k,
                                "Size": len(client.objects[k][0]),
                                "LastModified": client.objects[k][1],
                            }
                            for k in chunk
                        ]
                    }

        return _Paginator()

    # test helper: store an object with a chosen age
    def seed(self, key: str, content: str, modified: datetime) -> None:
        self.objects[key] = (content.encode(), modified, "")

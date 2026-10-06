"""S3-compatible artifact store (issue #27): AWS S3, Ceph, SeaweedFS, ...

``boto3`` is an optional dependency (the ``s3`` extra); it is imported when
the store is first built. Credentials come from boto3's usual chain
(``AWS_ACCESS_KEY_ID``/``AWS_SECRET_ACCESS_KEY``, profiles, instance roles) --
never from this project's settings, so they cannot leak through a config dump.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from bike_routing_agent.storage.artifacts import ARTIFACT_NAME, ArtifactInfo, media_type_for


def _is_missing(exc: Exception) -> bool:
    """Is this "no such *object*"?

    By error code, not HTTP status: a missing bucket is also a 404 but is a
    misconfiguration that must surface, not read as "artifact not found".
    (HEAD responses carry no body, so their code is the bare status "404".)
    """
    response = getattr(exc, "response", None) or {}
    return str(response.get("Error", {}).get("Code", "")) in ("NoSuchKey", "404", "NotFound")


class S3ArtifactStore:
    def __init__(
        self,
        *,
        bucket: str,
        prefix: str = "",
        client: Any = None,
        endpoint_url: str | None = None,
        region: str | None = None,
        path_style: bool = False,
    ) -> None:
        self._bucket = bucket
        self._prefix = prefix.strip("/") + "/" if prefix.strip("/") else ""
        self._client = client if client is not None else self._build_client(
            endpoint_url, region, path_style
        )

    @staticmethod
    def _build_client(endpoint_url: str | None, region: str | None, path_style: bool) -> Any:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on the environment
            raise RuntimeError(
                "ARTIFACT_BACKEND=s3 needs boto3; install the s3 extra: "
                "pip install 'bike-routing-agent[s3]'"
            ) from exc
        config = Config(s3={"addressing_style": "path" if path_style else "auto"})
        return boto3.client("s3", endpoint_url=endpoint_url, region_name=region, config=config)

    def _key(self, name: str) -> str:
        return f"{self._prefix}{name}"

    def put(self, name: str, content: str) -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=self._key(name),
            Body=content.encode(),
            ContentType=media_type_for(name),
        )

    def get(self, name: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=self._key(name))
        except Exception as exc:
            if _is_missing(exc):
                return None
            raise
        return bytes(response["Body"].read())

    def list_artifacts(self) -> Iterator[ArtifactInfo]:
        paginator = self._client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self._bucket, Prefix=self._prefix):
            for obj in page.get("Contents", []):
                name = obj["Key"][len(self._prefix) :]
                if ARTIFACT_NAME.match(name):
                    yield ArtifactInfo(
                        name=name, size=int(obj["Size"]), created_at=obj["LastModified"]
                    )

    def delete(self, name: str) -> bool:
        # Only names this service generates: anything else (path tricks, other
        # objects sharing the bucket) is not ours to remove.
        if not ARTIFACT_NAME.match(name):
            return False
        # S3 answers a delete of a missing key with success, so look first to
        # report honestly whether anything was removed.
        try:
            self._client.head_object(Bucket=self._bucket, Key=self._key(name))
        except Exception as exc:
            if _is_missing(exc):
                return False
            raise
        self._client.delete_object(Bucket=self._bucket, Key=self._key(name))
        return True

    def ping(self) -> dict[str, str]:
        """Bucket reachability for the readiness endpoint (never raises)."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except Exception:
            return {"status": "unavailable"}
        return {"status": "ok"}

    def presigned_url(self, name: str, ttl_s: int) -> str:
        return str(
            self._client.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": self._key(name)},
                ExpiresIn=ttl_s,
            )
        )

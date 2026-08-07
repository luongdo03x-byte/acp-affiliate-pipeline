"""Nơi chứa ảnh đã ghép.

Threads KHÔNG nhận file gửi trực tiếp -- máy chủ của Meta tự tải ảnh về từ URL
bạn cung cấp. Nên ảnh bắt buộc phải nằm ở một địa chỉ công khai.

Hai lựa chọn:

  local  Phục vụ từ chính app qua route /media/<file>. Chỉ dùng được khi máy có
         địa chỉ công khai (VPS, tunnel có tên miền cố định). Đây là mặc định
         cho giai đoạn chạy thử.

  s3     Đẩy lên Cloudflare R2 hoặc Amazon S3. URL cố định vĩnh viễn, không phụ
         thuộc máy bạn có đang bật hay không. Đây là lựa chọn cho chạy thật ở
         máy cá nhân.

Chọn bằng biến ACP_STORAGE. Phần còn lại của hệ thống không biết khác biệt.

Lưu ý có lợi: Meta tải ảnh về lúc tạo container rồi tự lưu trên CDN của họ, nên
bài ĐÃ đăng không chết dù nguồn ảnh sau đó hỏng. URL công khai chỉ cần sống đúng
lúc đăng.
"""
import mimetypes
import os


class LocalStorage:
    """Phục vụ ảnh từ chính app. Không sao chép đi đâu cả."""

    kind = "local"

    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def put(self, local_path: str) -> str:
        return f"{self.base_url}/{os.path.basename(local_path)}"

    def healthcheck(self) -> tuple:
        if "localhost" in self.base_url or "127.0.0.1" in self.base_url:
            return (False, "ACP_MEDIA_BASE_URL đang trỏ vào localhost — Meta sẽ không tải được ảnh. "
                           "Dùng ACP_STORAGE=s3, hoặc đặt base URL công khai.")
        return (True, f"phục vụ ảnh từ {self.base_url}")


class S3Storage:
    """Cloudflare R2 (tương thích S3) hoặc Amazon S3."""

    kind = "s3"

    def __init__(self, bucket: str, public_base: str, endpoint_url: str = None,
                 access_key: str = None, secret_key: str = None, region: str = "auto",
                 prefix: str = ""):
        import boto3  # nhập trong hàm để bản local không cần cài boto3

        self.bucket = bucket
        self.public_base = public_base.rstrip("/")
        self.prefix = prefix.strip("/")
        self.client = boto3.client(
            "s3", endpoint_url=endpoint_url, region_name=region,
            aws_access_key_id=access_key, aws_secret_access_key=secret_key,
        )

    def _key(self, local_path: str) -> str:
        name = os.path.basename(local_path)
        return f"{self.prefix}/{name}" if self.prefix else name

    def put(self, local_path: str) -> str:
        key = self._key(local_path)
        ctype = mimetypes.guess_type(local_path)[0] or "image/jpeg"
        with open(local_path, "rb") as fh:
            self.client.put_object(
                Bucket=self.bucket, Key=key, Body=fh, ContentType=ctype,
                # Ảnh đăng bài không đổi sau khi tạo -> cache dài.
                CacheControl="public, max-age=31536000, immutable",
            )
        return f"{self.public_base}/{key}"

    def healthcheck(self) -> tuple:
        try:
            self.client.head_bucket(Bucket=self.bucket)
            return (True, f"kết nối được bucket {self.bucket}")
        except Exception as e:
            return (False, f"không truy cập được bucket {self.bucket}: {e}")


def get_storage():
    """Đọc cấu hình từ biến môi trường."""
    kind = os.environ.get("ACP_STORAGE", "local").lower()
    if kind == "s3":
        bucket = os.environ.get("R2_BUCKET") or os.environ.get("S3_BUCKET")
        public = os.environ.get("ACP_MEDIA_BASE_URL")
        if not bucket or not public:
            raise RuntimeError("ACP_STORAGE=s3 cần cả R2_BUCKET và ACP_MEDIA_BASE_URL")
        return S3Storage(
            bucket=bucket,
            public_base=public,
            endpoint_url=os.environ.get("R2_ENDPOINT") or os.environ.get("S3_ENDPOINT"),
            access_key=os.environ.get("R2_ACCESS_KEY_ID") or os.environ.get("AWS_ACCESS_KEY_ID"),
            secret_key=os.environ.get("R2_SECRET_ACCESS_KEY") or os.environ.get("AWS_SECRET_ACCESS_KEY"),
            region=os.environ.get("R2_REGION", "auto"),
            prefix=os.environ.get("ACP_MEDIA_PREFIX", ""),
        )
    return LocalStorage(os.environ.get("ACP_MEDIA_BASE_URL", "http://localhost:5000/media"))

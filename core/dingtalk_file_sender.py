import json
import mimetypes
import os
import re
from pathlib import Path

import requests

from core.paths import OUTPUT_DIR


API_BASE = "https://api.dingtalk.com/v1.0"
OAPI_BASE = "https://oapi.dingtalk.com"


def find_pptx_paths(text):
    """
    Extract real PPTX paths from text.

    Safety rules:
    1. Only accept absolute .pptx paths under the configured output directory.
    2. Ignore wildcard / example paths such as *.pptx, ?.pptx, [abc].pptx.
    3. Ignore paths that do not exist, to avoid triggering native file send on examples.
    4. Deduplicate while preserving order.
    """
    import re
    from pathlib import Path

    text = str(text or "")
    if not text:
        return []

    candidates = re.findall(
        r"(/[^\s\]\)\}\>\"'`，。；;]+?\.pptx)",
        text,
        flags=re.IGNORECASE,
    )

    result = []
    seen = set()

    for raw in candidates:
        path = str(raw or "").strip()
        path = path.rstrip(".,;:，。；：、）)]}>\"'`")

        if not path:
            continue

        # Wildcard / glob / example paths must never trigger sending.
        if any(ch in path for ch in ["*", "?", "["]):
            continue

        if not path.lower().endswith(".pptx"):
            continue

        candidate = Path(path).resolve()
        if not candidate.is_relative_to(OUTPUT_DIR.resolve()) or not candidate.is_file():
            continue

        resolved = str(candidate)
        if resolved not in seen:
            result.append(resolved)
            seen.add(resolved)

    return result


class DingTalkFileSender:
    def __init__(self, app_key, app_secret):
        self.app_key = app_key
        self.app_secret = app_secret
        self._token = None

    def get_access_token(self):
        if self._token:
            return self._token

        url = f"{API_BASE}/oauth2/accessToken"
        resp = requests.post(
            url,
            json={
                "appKey": self.app_key,
                "appSecret": self.app_secret,
            },
            timeout=30,
        )
        resp.raise_for_status()
        data = resp.json()

        token = data.get("accessToken")
        if not token:
            raise RuntimeError(f"获取 accessToken 失败：{data}")

        self._token = token
        return token

    def upload_file(self, file_path):
        return self._upload_media(file_path, "file")

    def upload_image(self, file_path):
        return self._upload_media(file_path, "image")

    def _upload_media(self, file_path, media_type):
        token = self.get_access_token()
        p = Path(file_path).resolve()

        if not p.exists() or not p.is_file():
            raise RuntimeError(f"文件不存在：{p}")

        url = f"{OAPI_BASE}/media/upload"
        params = {
            "access_token": token,
            "type": media_type,
        }

        content_type = mimetypes.guess_type(str(p))[0] or "application/octet-stream"

        with p.open("rb") as f:
            files = {
                "media": (p.name, f, content_type)
            }
            resp = requests.post(
                url,
                params=params,
                files=files,
                timeout=120,
            )

        resp.raise_for_status()
        data = resp.json()

        media_id = data.get("media_id") or data.get("mediaId")
        if not media_id:
            raise RuntimeError(f"上传媒体失败：{data}")

        return media_id

    def _post_openapi(self, url, body):
        token = self.get_access_token()

        headers = {
            "Content-Type": "application/json",
            "x-acs-dingtalk-access-token": token,
        }

        resp = requests.post(
            url,
            headers=headers,
            json=body,
            timeout=60,
        )

        text = resp.text
        try:
            data = resp.json()
        except Exception:
            data = {"raw": text}

        if resp.status_code >= 400:
            raise RuntimeError(f"发送文件失败 HTTP {resp.status_code}：{data}")

        # 新版接口有时成功返回很简单；这里只拦截明确错误
        code = data.get("code") or data.get("errcode")
        if code not in (None, "", 0, "0", "ok", "OK"):
            raise RuntimeError(f"发送文件失败：{data}")

        return data

    def send_file_private(self, robot_code, user_id, file_path):
        p = Path(file_path).resolve()
        media_id = self.upload_file(p)

        ext = p.suffix.lstrip(".") or "pptx"

        body = {
            "robotCode": robot_code,
            "userIds": [user_id],
            "msgKey": "sampleFile",
            "msgParam": json.dumps(
                {
                    "mediaId": media_id,
                    "fileName": p.name,
                    "fileType": ext,
                },
                ensure_ascii=False,
            ),
        }

        return self._post_openapi(
            f"{API_BASE}/robot/oToMessages/batchSend",
            body,
        )

    def send_file_group(self, robot_code, open_conversation_id, file_path):
        p = Path(file_path).resolve()
        media_id = self.upload_file(p)

        ext = p.suffix.lstrip(".") or "pptx"

        body = {
            "robotCode": robot_code,
            "openConversationId": open_conversation_id,
            "msgKey": "sampleFile",
            "msgParam": json.dumps(
                {
                    "mediaId": media_id,
                    "fileName": p.name,
                    "fileType": ext,
                },
                ensure_ascii=False,
            ),
        }

        return self._post_openapi(
            f"{API_BASE}/robot/groupMessages/send",
            body,
        )

    def _image_body(self, robot_code, media_id):
        return {
            "robotCode": robot_code,
            "msgKey": "sampleImageMsg",
            "msgParam": json.dumps({"photoURL": media_id}, ensure_ascii=False),
        }

    def send_image_private(self, robot_code, user_id, image_path):
        body = self._image_body(robot_code, self.upload_image(image_path))
        body["userIds"] = [user_id]
        return self._post_openapi(f"{API_BASE}/robot/oToMessages/batchSend", body)

    def send_image_group(self, robot_code, open_conversation_id, image_path):
        body = self._image_body(robot_code, self.upload_image(image_path))
        body["openConversationId"] = open_conversation_id
        return self._post_openapi(f"{API_BASE}/robot/groupMessages/send", body)

    def send_file_for_raw(self, raw, file_path):
        robot_code = raw.get("robotCode") or self.app_key
        conversation_type = str(raw.get("conversationType", ""))

        # 单聊：优先用 senderStaffId
        if conversation_type == "1" or raw.get("senderStaffId"):
            user_id = raw.get("senderStaffId") or raw.get("senderId")
            if not user_id:
                raise RuntimeError("未找到 senderStaffId / senderId，无法发送单聊文件。")
            return self.send_file_private(robot_code, user_id, file_path)

        # 群聊：用 conversationId/openConversationId
        open_conversation_id = raw.get("openConversationId") or raw.get("conversationId")
        if not open_conversation_id:
            raise RuntimeError("未找到 conversationId，无法发送群聊文件。")

        return self.send_file_group(robot_code, open_conversation_id, file_path)

    def send_image_for_raw(self, raw, image_path):
        robot_code = raw.get("robotCode") or self.app_key
        if str(raw.get("conversationType", "")) == "1" or raw.get("senderStaffId"):
            user_id = raw.get("senderStaffId") or raw.get("senderId")
            if not user_id:
                raise RuntimeError("未找到 senderStaffId / senderId，无法发送单聊图片。")
            return self.send_image_private(robot_code, user_id, image_path)
        open_conversation_id = raw.get("openConversationId") or raw.get("conversationId")
        if not open_conversation_id:
            raise RuntimeError("未找到 conversationId，无法发送群聊图片。")
        return self.send_image_group(robot_code, open_conversation_id, image_path)

    def send_text_for_raw(self, raw, content):
        """Send a short proactive recovery notice to the original conversation."""
        robot_code = raw.get("robotCode") or self.app_key
        body = {
            "robotCode": robot_code,
            "msgKey": "sampleText",
            "msgParam": json.dumps({"content": str(content or "")}, ensure_ascii=False),
        }
        if str(raw.get("conversationType", "")) == "1" or raw.get("senderStaffId"):
            user_id = raw.get("senderStaffId") or raw.get("senderId")
            if not user_id:
                raise RuntimeError("恢复通知缺少 senderStaffId / senderId。")
            body["userIds"] = [user_id]
            return self._post_openapi(f"{API_BASE}/robot/oToMessages/batchSend", body)

        open_conversation_id = raw.get("openConversationId") or raw.get("conversationId")
        if not open_conversation_id:
            raise RuntimeError("恢复通知缺少 conversationId。")
        body["openConversationId"] = open_conversation_id
        return self._post_openapi(f"{API_BASE}/robot/groupMessages/send", body)

"""Deterministic pre-model rules. Rejections never call a model or tools."""

import re
import unicodedata

from pydantic import BaseModel

from .governance import DEFAULT_PORTS, GatewayPolicy


class GatewayDecision(BaseModel):
    allowed: bool
    code: str
    message: str
    llm_calls: int = 0


SENSITIVE_PORTS = set(DEFAULT_PORTS)


def normalize_text(prompt: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFKD", prompt.lower().replace("đ", "d"))
        if not unicodedata.combining(c)
    )


def evaluate_request(prompt: str, policy: GatewayPolicy | None = None) -> GatewayDecision:
    policy = policy or GatewayPolicy()
    text = normalize_text(prompt)

    def reject(code, message):
        return GatewayDecision(allowed=False, code=code, message=message)

    if any(normalize_text(term) in text for term in policy.blocked_terms):
        return reject("blocked_term", "Static Gateway: yêu cầu chứa từ khóa bị chặn bởi chính sách hiện tại.")
    if re.search(
        r"\b(viet (?:ma|code|ham|bai|tho)|write (?:code|a function|an essay|a poem)|giai bai|dich (?:sang|van ban)|translate|la gi|what is|explain (?:what|how))\b",
        text,
    ):
        return reject(
            "out_of_scope",
            "Static Gateway: chỉ nhận yêu cầu quản lý hạ tầng; không trả lời kiến thức chung hoặc viết code.",
        )
    for clause in re.split(r"[;\n]|\.(?:\s|$)", text):
        negated = re.match(r"\s*(khong|never|do not|without)\b", clause)
        if not negated and re.search(
            r"\b(xoa|delete|destroy|remove|prune|format)\b|down\s+-v|rm\s+-rf", clause
        ):
            return reject(
                "destructive_operation",
                "Static Gateway: demo chưa cho phép xóa tài nguyên/dữ liệu. Cần một quy trình và phạm vi xóa riêng.",
            )
        if not negated and re.search(r"\b(mo|public|publish|expose|bind)\b", clause):
            if any(int(port) in policy.blocked_ports for port in re.findall(r"\b\d{2,5}\b", clause)):
                return reject(
                    "sensitive_port", "Static Gateway: chặn publish port quản trị hoặc database nhạy cảm."
                )
            if policy.block_sensitive_services and re.search(
                r"\b(?:public|publish|expose|mo)\s+(?:(?:port|cong|cho|ra|ngoai|internet|the)\s+){0,3}(?:database|postgres\w*|redis|docker socket)\b",
                clause,
            ):
                return reject(
                    "sensitive_service",
                    "Static Gateway: database/cache/Docker socket phải ở mạng nội bộ trong demo.",
                )
        if not negated and re.search(
            r"privileged|docker\.sock|network[_ -]mode\s*[:=]?\s*host|0\.0\.0\.0", clause
        ):
            return reject(
                "unsafe_access",
                "Static Gateway: không cho phép privileged, Docker socket, host network hoặc bind mọi interface.",
            )

    def has_term(terms):
        return any(re.search(r"\b" + re.escape(normalize_text(term)) + r"\b", text) for term in terms)

    action = has_term(policy.allowed_actions)
    target = has_term(policy.infrastructure_terms)
    if not action or not target:
        return reject(
            "out_of_scope",
            "Static Gateway: hãy nêu thao tác hạ tầng và đối tượng cụ thể, ví dụ 'Tăng backend lên 3 bản sao'.",
        )
    return GatewayDecision(
        allowed=True, code="accepted", message="Yêu cầu thuộc phạm vi hạ tầng và qua kiểm tra rủi ro tĩnh."
    )


def enforce_request(prompt: str, policy: GatewayPolicy | None = None):
    decision = evaluate_request(prompt, policy)
    if not decision.allowed:
        raise ValueError(f"{decision.message} [LLM calls: 0]")
    return decision

# Security Code Review Checklist: security.py + deps.py

**Date**: 2026-06-12
**Files**: `backend/core/security.py`, `backend/api/deps.py`

## Exception Handling

- [X] CHECK-001: deps.py 捕获 `(JWTError, JWSError)`，签名无效返回 401 而非 500
- [X] CHECK-002: decode_token 中 `sub` 用 `.get()` 取值，缺失 sub 时抛出 JWTError 而非 KeyError
- [X] CHECK-003: decode_token 内 jwt.decode() 异常被 try/except 捕获并记录日志
- [X] CHECK-004: 全局异常处理能捕获所有 JWT 相关异常（chain verify）

## Logic / Security

- [X] CHECK-005: TokenPayload.type 默认值为空字符串 ""，缺失 type 的 token 会被拒绝
- [X] CHECK-006: create_access_token 和 create_refresh_token 内部用 str() 转换 user_id
- [X] CHECK-007: is_account_locked 逻辑正确处理 locked_until=None 和过期锁定
- [X] CHECK-008: record_failed_login 达到阈值触发锁定时记录日志

## Resource Management

- [X] CHECK-009: get_current_user 中 session 使用 `async with session` 而非 `async with session.begin()` 确保连接释放
- [X] CHECK-010: user 查询后 None 检查在 session 外进行（避免持有 session 做业务判断）

## Logging

- [X] CHECK-011: decode_token 失败时记录 token_preview（前8字符）不泄漏完整 token
- [X] CHECK-012: deps.py token 无效、token 类型错误、用户不存在、用户停用 4 个分支均有 logger.warning
- [X] CHECK-013: security.py token 创建、验证失败、账号锁定 3 个事件均有日志
- [X] CHECK-014: 日志使用 structlog 结构化格式（事件名 + key=value 参数）

## Code Quality

- [X] CHECK-015: user_scoped_query 死代码已删除
- [X] CHECK-016: deps.py 未使用 import（UUID）已清理
- [X] CHECK-017: security.py 未使用 import（无）已清理

## Cross-File Consistency

- [X] CHECK-018: security.py 导入 backend.utils.logging 的 get_logger
- [X] CHECK-019: deps.py 导入 backend.utils.logging 的 get_logger
- [X] CHECK-020: deps.py 从 jose 同时导入 JWTError 和 JWSError

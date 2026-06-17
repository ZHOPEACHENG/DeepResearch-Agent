/**
 * Shared form validation rules used across auth pages.
 *
 * Password policy: ≥8 chars, at least one uppercase, one lowercase, one digit.
 */

/** Creates a password strength validator that reports all failures at once */
export function createPasswordValidator() {
  return (_rule: unknown, value: string, callback: (err?: Error) => void) => {
    const errors: string[] = []
    if (!/[A-Z]/.test(value)) errors.push('密码必须包含大写字母')
    if (!/[a-z]/.test(value)) errors.push('密码必须包含小写字母')
    if (!/[0-9]/.test(value)) errors.push('密码必须包含数字')
    if (errors.length > 0) {
      callback(new Error(errors.join('；')))
    } else {
      callback()
    }
  }
}

/** Creates a confirm-password validator that compares against a source ref */
export function createConfirmPasswordValidator(
  getPassword: () => string,
  label = '密码',
) {
  return (_rule: unknown, value: string, callback: (err?: Error) => void) => {
    if (value !== getPassword()) {
      callback(new Error(`两次输入的${label}不一致`))
    } else {
      callback()
    }
  }
}

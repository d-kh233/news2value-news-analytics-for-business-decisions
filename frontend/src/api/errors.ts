/** Ошибка API в форме бэкенда: {"error": "код", "detail": "описание"}. */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;

  constructor(status: number, code: string, detail: string) {
    super(detail);
    this.status = status;
    this.code = code;
  }
}

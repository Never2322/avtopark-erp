// Обращения к серверу: один адрес /api, защита от подделки запроса (CSRF) подставляется сама.
export class ApiError extends Error {
  status: number;
  code: string;
  field?: string | null;
  fields?: Record<string, string>;
  constructor(status: number, code: string, message: string, field?: string | null, fields?: Record<string, string>) {
    super(message);
    this.status = status;
    this.code = code;
    this.field = field;
    this.fields = fields;
  }
}

let csrf = "";
export const setCsrf = (token: string) => {
  csrf = token;
};

export type Params = Record<string, string | number | boolean | null | undefined>;

export function qs(params?: Params): string {
  if (!params) return "";
  const q = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== null && v !== "" && v !== false) q.set(k, String(v));
  const s = q.toString();
  return s ? "?" + s : "";
}

export async function api<T = any>(method: string, url: string, body?: unknown, params?: Params, signal?: AbortSignal): Promise<T> {
  const headers: Record<string, string> = {};
  // Файлы уходят формой: тип запроса с границей браузер ставит сам, вручную его задавать нельзя.
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  if (body !== undefined && !isForm) headers["Content-Type"] = "application/json";
  if (method !== "GET" && csrf) headers["X-CSRF-Token"] = csrf;
  let res: Response;
  try {
    res = await fetch("/api" + url + qs(params), {
      method,
      headers,
      body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
      credentials: "same-origin",
      signal,
    });
  } catch {
    throw new ApiError(0, "network", "Нет связи с сервером. Проверьте интернет и повторите.");
  }
  const isJson = (res.headers.get("content-type") || "").includes("json");
  const data = isJson ? await res.json() : null;
  if (!res.ok) {
    const err = new ApiError(res.status, data?.code ?? "http_" + res.status, data?.message ?? (res.status === 413 ? "Файлы слишком большие: за один раз можно отправить не больше 60 МБ" : "Ошибка " + res.status), data?.field, data?.fields);
    if (res.status === 401 && err.code !== "bad_credentials") window.dispatchEvent(new Event("auth:expired"));
    throw err;
  }
  return data as T;
}

export const get = <T = any>(url: string, params?: Params) => api<T>("GET", url, undefined, params);
export const post = <T = any>(url: string, body: unknown = {}) => api<T>("POST", url, body);
export const patch = <T = any>(url: string, body: unknown = {}) => api<T>("PATCH", url, body);
/** Отправка файлов: форма FormData с полями и файлами. */
export const upload = <T = any>(url: string, form: FormData) => api<T>("POST", url, form);
export const put = <T = any>(url: string, body: unknown = {}) => api<T>("PUT", url, body);

export const errorText = (e: unknown): string => (e instanceof ApiError ? e.message : "Что-то пошло не так. Повторите попытку.");

export type AuthenticatedUser = {
  username: string;
  role: "platform_operator";
  role_label: string;
  capabilities: string[];
};

async function readUser(response: Response): Promise<AuthenticatedUser> {
  if (!response.ok) throw new Error("账号或密码不正确");
  return response.json() as Promise<AuthenticatedUser>;
}

export async function fetchSession(): Promise<AuthenticatedUser | null> {
  const response = await fetch("/api/auth/session");
  if (response.status === 401) return null;
  return readUser(response);
}

export async function login(
  username: string,
  password: string,
): Promise<AuthenticatedUser> {
  return readUser(
    await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    }),
  );
}

export async function logout(): Promise<void> {
  const response = await fetch("/api/auth/logout", { method: "POST" });
  if (!response.ok) throw new Error("退出失败，请重试");
}

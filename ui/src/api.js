// Calls to the Python server. Writes carry X-RCA-UI so another site cannot trigger them.
async function request(path, options = {}) {
  const res = await fetch(path, options);
  const body = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(body.error || `${res.status} ${res.statusText}`);
  return body;
}

const post = (path, body) =>
  request(path, {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-RCA-UI": "1" },
    body: JSON.stringify(body || {}),
  });

export const api = {
  systems: () => request("/api/systems"),
  cases: (system) => request(`/api/cases?system=${encodeURIComponent(system)}`),
  sessions: () => request("/api/sessions"),
  session: (id) => request(`/api/sessions/${id}`),
  answer: (id) => request(`/api/sessions/${id}/answer`),
  run: (system, kase, backend) => post("/api/run", { system, case: kase, backend }),
  stop: (id) => post(`/api/sessions/${id}/stop`),
  profile: (system, language) => request(`/api/profile?system=${encodeURIComponent(system)}&language=${language}`),
  runProfile: (system, backend, overwrite, windows) => post("/api/profile/run", { system, backend, overwrite, windows }),
  translateProfile: (system, backend, overwrite) => post("/api/profile/translate", { system, backend, overwrite }),
  settings: () => request("/api/settings"),
  saveSettings: (s) => post("/api/settings", s),
  stream: (id, from) => new EventSource(`/api/sessions/${id}/stream?from=${from}`),
};

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  vus: 10,
  iterations: 150,
  maxDuration: '120s',
};

export default function () {
  const runId = __ENV.RUN_ID || 'run1';
  const username = `k6_${runId}_${__VU}_${__ITER}`;
  const payload = JSON.stringify({
    username: username,
    email: `${username}@test.com`,
    password: '12345',
  });
  const res = http.post(`${BASE_URL}/api/v1/auth/signup`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(res, {
    'status is 201': (r) => r.status === 201,
    'username matches': (r) => r.json().username === username,
  });
  sleep(0.1);
}

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  vus: 10,
  iterations: 100,
  maxDuration: '120s',
};

export default function () {
  const payload = JSON.stringify({
    username: 'ali',
    password: 'wrong123',
  });
  const res = http.post(`${BASE_URL}/api/v1/auth/login`, payload, {
    headers: { 'Content-Type': 'application/json' },
  });
  check(res, {
    'status is 401': (r) => r.status === 401,
    'detail is invalid': (r) => r.json().detail === 'invalid username or password',
  });
  sleep(0.05);
}

import http from 'k6/http';
import { check, sleep } from 'k6';

const BASE_URL = __ENV.BASE_URL || 'http://localhost:8000';

export const options = {
  vus: 10,
  duration: '30s',
};

export default function () {
  const isHit = Math.random() < 0.2;
  let username;
  let expected;
  if (isHit) {
    const n = Math.floor(Math.random() * 10000);
    username = 'seed' + String(n).padStart(6, '0');
    expected = false;
  } else {
    username = 'miss' + Math.random().toString(36).slice(2, 10);
    expected = true;
  }
  const res = http.get(`${BASE_URL}/api/v1/auth/check-username?username=${username}`);
  check(res, {
    'status is 200': (r) => r.status === 200,
    'available matches expectation': (r) => r.json().available === expected,
  });
  sleep(0.1);
}

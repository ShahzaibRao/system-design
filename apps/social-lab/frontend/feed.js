let currentUsername = '';

function authHeaders() {
  return {
    'Content-Type': 'application/json',
    'Authorization': 'Bearer ' + localStorage.getItem('social_lab_token'),
  };
}

function loadFeed(incrementViews) {
  fetch('/api/v1/feed')
    .then(function (res) { return res.json(); })
    .then(function (posts) {
      const postsDiv = document.getElementById('posts');
      postsDiv.textContent = '';
      if (posts.length === 0) {
        postsDiv.textContent = 'No posts yet';
        return;
      }
      posts.forEach(function (post) {
        renderPost(post, postsDiv, incrementViews);
      });
    });
}

function renderPost(post, postsDiv, incrementViews) {
  const postDiv = document.createElement('div');
  postDiv.className = 'post';

  const authorDiv = document.createElement('div');
  authorDiv.className = 'post-author';
  authorDiv.textContent = post.username;
  postDiv.appendChild(authorDiv);

  const timeDiv = document.createElement('div');
  timeDiv.className = 'post-time';
  timeDiv.textContent = post.created_at || '';
  postDiv.appendChild(timeDiv);

  const contentP = document.createElement('p');
  contentP.textContent = post.content;
  postDiv.appendChild(contentP);

  const metaDiv = document.createElement('div');
  metaDiv.className = 'post-meta';
  metaDiv.textContent = post.likes_count + ' likes | ' + post.views + ' views | ' + post.comments.length + ' comments';
  postDiv.appendChild(metaDiv);

  const actionsDiv = document.createElement('div');
  actionsDiv.className = 'post-actions';
  const likeButton = document.createElement('button');
  likeButton.textContent = 'Like';
  likeButton.addEventListener('click', function () {
    fetch('/api/v1/posts/' + post.id + '/like', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ username: currentUsername }),
    }).then(function () { loadFeed(false); });
  });
  actionsDiv.appendChild(likeButton);
  postDiv.appendChild(actionsDiv);

  const commentsDiv = document.createElement('div');
  post.comments.forEach(function (comment) {
    const commentP = document.createElement('p');
    commentP.textContent = comment.username + ': ' + comment.text;
    commentsDiv.appendChild(commentP);
  });
  postDiv.appendChild(commentsDiv);

  const commentInput = document.createElement('input');
  commentInput.type = 'text';
  commentInput.placeholder = 'Write a comment';
  commentInput.className = 'comment-input';
  postDiv.appendChild(commentInput);

  const commentButton = document.createElement('button');
  commentButton.textContent = 'Comment';
  commentButton.addEventListener('click', function () {
    const text = commentInput.value.trim();
    if (!text) {
      alert('Please write a comment first');
      return;
    }
    fetch('/api/v1/posts/' + post.id + '/comment', {
      method: 'POST',
      headers: authHeaders(),
      body: JSON.stringify({ username: currentUsername, text: text }),
    }).then(function () { loadFeed(false); });
  });
  postDiv.appendChild(commentButton);

  postsDiv.appendChild(postDiv);

  if (incrementViews) {
    fetch('/api/v1/posts/' + post.id + '/view', { method: 'POST' });
  }
}

document.getElementById('create-post-form').addEventListener('submit', function (event) {
  event.preventDefault();
  const messageDiv = document.getElementById('message');
  messageDiv.textContent = '';
  const payload = {
    username: currentUsername,
    content: document.getElementById('post-content').value.trim(),
  };
  fetch('/api/v1/posts', {
    method: 'POST',
    headers: authHeaders(),
    body: JSON.stringify(payload),
  })
    .then(function (res) { return res.json(); })
    .then(function (data) {
      if (data.id) {
        messageDiv.textContent = 'Post created';
        messageDiv.style.color = 'green';
        document.getElementById('post-content').value = '';
        loadFeed(false);
      } else {
        messageDiv.textContent = data.detail || 'Post failed';
        messageDiv.style.color = 'red';
      }
    });
});

document.getElementById('logout-button').addEventListener('click', function () {
  const token = localStorage.getItem('social_lab_token');
  fetch('/api/v1/auth/logout', {
    method: 'POST',
    headers: { 'Authorization': 'Bearer ' + token },
  }).finally(function () {
    localStorage.removeItem('social_lab_token');
    localStorage.removeItem('social_lab_username');
    window.location.href = '/static/login.html';
  });
});

(function initFeed() {
  const token = localStorage.getItem('social_lab_token');
  if (!token) {
    window.location.href = '/static/login.html';
    return;
  }
  fetch('/api/v1/auth/me', {
    headers: { 'Authorization': 'Bearer ' + token },
  })
    .then(function (res) {
      if (!res.ok) {
        throw new Error('invalid session');
      }
      return res.json();
    })
    .then(function (data) {
      currentUsername = data.username;
      document.getElementById('logged-in-user').textContent = 'Logged in as ' + currentUsername;
      loadFeed(true);
    })
    .catch(function () {
      localStorage.removeItem('social_lab_token');
      localStorage.removeItem('social_lab_username');
      window.location.href = '/static/login.html';
    });
})();


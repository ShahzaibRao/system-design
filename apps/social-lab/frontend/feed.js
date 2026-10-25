
function getActingUsername() {
  return document.getElementById('post-username').value.trim();
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
    const username = getActingUsername();
    if (!username) {
      alert('Please type a username in the create post form first');
      return;
    }
    fetch('/api/v1/posts/' + post.id + '/like', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username }),
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
    const username = getActingUsername();
    const text = commentInput.value.trim();
    if (!username || !text) {
      alert('Please type a username and a comment first');
      return;
    }
    fetch('/api/v1/posts/' + post.id + '/comment', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username: username, text: text }),
    }).then(function () { loadFeed(false); });
  });
  postDiv.appendChild(commentButton);

  postsDiv.appendChild(postDiv);

  if (incrementViews) {
    fetch('/api/v1/posts/' + post.id + '/view', { method: 'POST' });
  }
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

document.getElementById('create-post-form').addEventListener('submit', function (event) {
  event.preventDefault();
  const messageDiv = document.getElementById('message');
  messageDiv.textContent = '';
  const payload = {
    username: document.getElementById('post-username').value.trim(),
    content: document.getElementById('post-content').value.trim(),
  };
  fetch('/api/v1/posts', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
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

loadFeed(true);

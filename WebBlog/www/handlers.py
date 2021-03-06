#!/usr/bin/env python3
# -*- coding:utf-8 -*-
"""Handler functions for web pages.

Handler functions for router paths and some helper methods.
"""
import re
import time
import json
import markdown2
import logging
import hashlib
import base64
import asyncio
from apis import *
from coroweb import *
from models.models import *
from confs.config import *

logging.basicConfig(level=logging.INFO)
_RE_EMAIL = re.compile(r'^[a-z0-9\.\-\_]+\@[a-z0-9\-\_]+(\.[a-z0-9\-\_]+){1,4}$')
_RE_SHA1 = re.compile(r'^[0-9a-f]{40}$')
COOKIE_NAME = 'awesession'
_COOKIE_KEY = configs.session.secret


def user2cookie(user, max_age):
    """Generate cookie string by user.

    Combine user info into cookie in the format of
    id-expires-sha1(id, password, expires, secret_key).

    Args:
        user: User object.
        max_age: expire time length.

    Returns:
        A cookie string referring to the request user.
        For example: ()

    Raises:
        Exception: Cannot find max_age parameter.
    """
    expires = str(int(time.time() + max_age))
    s = '%s-%s-%s-%s' % (user.id, user.password, expires, _COOKIE_KEY)
    cookie = [user.id, expires, hashlib.sha1(s.encode('utf-8')).hexdigest()]
    return '-'.join(cookie)


@asyncio.coroutine
def cookie2user(cookie):
    """parse cookie and load user if cookie is valid"""
    if not cookie:
        return None
    try:
        s = cookie.split('-')
        if len(s) != 3:
            return None
        uid, expires, sha1 = s
        if int(expires) < time.time():
            return None
        user = yield from User.find(uid)
        if user is None:
            return None
        string = '%s-%s-%s-%s' % (uid, user.password, expires, _COOKIE_KEY)
        if sha1 != hashlib.sha1(string.encode('utf-8')).hexdigest():
            logging.info('invalid sha1')
            return None
        user.password = '******'
        return user
    except Exception as e:
        logging.exception(e)
        return None


def check_admin(request):
    if request.__user__ is None or not request.__user__.admin == 1:
        raise APIPermissionError()


def get_page_index(page_str):
    p = 1
    try:
        p = int(page_str)
    except ValueError as e:
        pass
    if p < 1:
        p = 1
    return p


def text2html(text):
    lines = map(lambda s: '<p>%s</p>' % s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;'), filter(lambda s: s.strip() != '', text.split('\n')))
    return ''.join(lines)


# 博客主页
@get('/')
async def index(request, *, page='1'):
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')
    page = Page(num, page_index=page_index)
    logging.info(page)
    if num == 0:
        blogs = []
    else:
        blogs = await Blog.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return {
        '__template__': 'blogs.html',
        'page': page,
        'blogs': blogs,
        '__user__': request.__user__
    }


# 日志展示页
@get('/blog/{id}')
async def get_blog(request, *, id):
    blog = await Blog.find(id)
    comments = await Comment.findAll('blog_id=?', [id], orderBy='created_at desc')
    for c in comments:
        c.html_content = text2html(c.content)
    blog.html_content = markdown2.markdown(blog.content)
    return {
        '__template__': 'blog.html',
        'blog': blog,
        '__user__': request.__user__,
        'comments': comments
    }


@get('/manage/')
def manage():
    return 'redirect:/manage/comments'


@get('/manage/comments')
def manage_comments(request, *, page='1'):
    return {
        '__template__': 'manage_comments.html',
        '__user__': request.__user__,
        'page_index': get_page_index(page)
    }


@get('/manage/users')
def manage_users(request, *, page='1'):
    return {
        '__template__': 'manage_users.html',
        '__user__': request.__user__,
        'page_index': get_page_index(page)
    }


@get('/manage/blogs')
def manage_blogs(request, *, page='1'):
    return {
        '__template__': 'manage_blogs.html',
        '__user__': request.__user__,
        'page_index': get_page_index(page)
    }


@get('/manage/blogs/create')
def manage_create_blog(request):
    return {
        '__template__': 'manage_blog_edit.html',
        '__user__': request.__user__,
        'id': '',
        'action': '/api/blogs'
    }


@get('/manage/blogs/edit')
def manage_edit_blog(request, *, id):
    return {
        '__template__': 'manage_blog_edit.html',
        '__user__': request.__user__,
        'id': id,
        'action': '/api/blogs/%s' % id
    }


@get('/register')
def register():
    logging.info('register handler function')
    return {'__template__': 'register.html'}


@get('/signin')
def signin():
    return {'__template__': 'signin.html'}


@get('/signout')
def signout(request):
    referer = request.headers.get('Referer')
    response = web.HTTPFound(referer or '/')
    response.set_cookie(COOKIE_NAME, '-deleted-', max_age=0, httponly=True)
    logging.info('user signed out.')
    return response


@get('/api/comments')
async def api_comments(*, page='1'):
    page_index = get_page_index(page)
    num = await Comment.findNumber('count(id)')
    page = Page(num, page_index)
    if num == 0:
        return dict(page=page, comments=())
    comments = await Comment.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return dict(page=page, comments=comments)


@post('/api/blogs/{id}/comments')
async def api_create_comment(request, *, id, content):
    user = request.__user__
    if user is None:
        raise APIPermissionError('Please sign in first.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog = await Blog.find(id)
    if blog is None:
        raise APIResourceNotFoundError('Blog does not exist.')
    comment = Comment(blog_id=blog.id, user_id=user.id, user_name=user.name, user_image=user.image, content=content.strip())
    await comment.save()
    return comment


@post('/api/comments/{id}/delete')
async def api_delete_comment(request, *, id):
    check_admin(request)
    comment = await Comment.find(id)
    if comment is None:
        raise APIResourceNotFoundError('Comment does not exist.')
    await comment.remove()
    return dict(id=id)


# 查找某一页的所有博客
@get('/api/blogs')
async def api_blogs(*, page='1'):
    page_index = get_page_index(page)
    num = await Blog.findNumber('count(id)')
    page = Page(num, page_index)
    if num == 0:
        return dict(page=page, blogs={})
    blogs = await Blog.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return dict(page=page, blogs=blogs)


# 查找某一篇博客
@get('/api/blogs/{id}')
async def api_get_blog(*, id):
    blog = await Blog.find(id)
    return blog


# 创建博客API
@post('/api/blogs')
async def api_create_blog(request, *, name, summary, content):
    check_admin(request)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog = Blog(user_id=request.__user__.id, user_name=request.__user__.name, user_image=request.__user__.image, name=name.strip(), summary=summary.strip(), content=content.strip())
    await blog.save()
    return blog


# 更新博客API
@post('/api/blogs/{id}')
async def api_update_blog(request, *, id, name, summary, content):
    check_admin(request)
    blog = await Blog.find(id)
    if blog:
        logging.info(blog.name)
    if not name or not name.strip():
        raise APIValueError('name', 'name cannot be empty.')
    if not summary or not summary.strip():
        raise APIValueError('summary', 'summary cannot be empty.')
    if not content or not content.strip():
        raise APIValueError('content', 'content cannot be empty.')
    blog.name = name.strip()
    blog.summary = summary.strip()
    blog.content = content.strip()
    await blog.update()
    return blog


# 删除博客API
@post('/api/blogs/{id}/delete')
async def api_delete_blog(request, *, id):
    check_admin(request)
    blog = await Blog.find(id)
    await blog.remove()
    return dict(id=id)


@get('/api/users')
async def api_get_users(*, page='1'):
    page_index = get_page_index(page)
    num = await User.findNumber('count(id)')
    page = Page(num, page_index)
    if num == 0:
        return dict(page=page, users=())
    users = await User.findAll(orderBy='created_at desc', limit=(page.offset, page.limit))
    return dict(page=page, users=users)


# 用户注册API
@post('/api/users')
async def api_register_user(*, email, name, password):
    logging.warning('invoking register api...')
    if not name or not name.strip():
        raise APIError('name')
    if not email or not _RE_EMAIL.match(email):
        raise APIError('email')
    if not password or not _RE_SHA1.match(password):
        raise APIError('password')
    users = await User.findAll('email=?', [email])
    if len(users) > 0:
        raise APIError('register:failed', 'email', 'Email is already in use.')
    # save user to db
    uid = next_id()
    sha1_password = '%s:%s' % (uid, password)
    user = User(id=uid, name=name.strip(), email=email,
                password=hashlib.sha1(sha1_password.encode('utf-8')).hexdigest(),
                image='http://www.gravatar.com/avatar/%s?d=mm&s=120' % hashlib.md5(email.encode('utf-8')).hexdigest())
    await user.save()
    # make session cookie
    response = web.Response()
    response.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.password = '******'
    response.content_type = 'application/json'
    response.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return response


# 验证用户API
@post('/api/authenticate')
async def authenticate(*, email, password):
    if not email:
        raise APIError('email', 'Invalid email')
    if not password:
        raise APIError('password', 'Invalid password')
    users = await User.findAll('email=?', [email])
    if len(users) == 0:
        raise APIValueError('email', 'Email not exist.')
    # always will be first one
    user = users[0]
    sha1 = hashlib.sha1()
    sha1.update(user.id.encode('utf-8'))
    sha1.update(b':')
    sha1.update(password.encode('utf-8'))
    if user.password != sha1.hexdigest():
        raise APIValueError('password', 'Invalid password.')
    response = web.Response()
    response.set_cookie(COOKIE_NAME, user2cookie(user, 86400), max_age=86400, httponly=True)
    user.password = '******'
    response.content_type = 'application/json'
    response.body = json.dumps(user, ensure_ascii=False).encode('utf-8')
    return response

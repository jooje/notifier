"""
General formatting and rendering helpers for digest notifications.
"""

import datetime
import logging

from django.conf import settings
from django.template.loader import get_template
from django.template import Context
from django.utils.html import strip_tags
from django.utils.translation import ugettext as _
from statsd import statsd

from notifier.user import UsernameCipher

# maximum number of threads to display per course
MAX_COURSE_THREADS = 30
# maximum number of items (posts) to display per thread
MAX_THREAD_ITEMS = 10
# maximum number of characters to allow in thread title, before truncating
THREAD_TITLE_MAXLEN = 140
# maximum number of characters to allow in thread post, before truncating
THREAD_ITEM_MAXLEN = 140


logger = logging.getLogger(__name__)


def _trunc(s, length):
    """
    Formatting helper.

    Truncate the string `s` to no more than `length`, using ellipsis and
    without chopping words.

    >>> _trunc("one two three", 13)
    'one two three'
    >>> _trunc("one two three", 12)
    'one two...'
    """
    s = s.strip()
    if len(s) <= length:
        # nothing to do
        return s
    # truncate, taking an extra -3 off the orig string for the ellipsis itself
    return s[:length - 3].rsplit(' ', 1)[0].strip() + '...'


def _make_text_list(values):
    """
    Formatting helper.

    Make a string containing a natural language list composed of the
    given items.

    >>> _make_text_list([])
    ''
    >>> _make_text_list(['spam'])
    'spam'
    >>> _make_text_list(['spam', 'eggs'])
    u'spam and eggs'
    >>> _make_text_list(['spam', 'eggs', 'beans'])
    u'spam, eggs, and beans'
    >>> _make_text_list(['spam', 'eggs', 'beans', 'cheese'])
    u'spam, eggs, beans, and cheese'
    """
    # Translators: This string separates two items in a pair (e.g.
    # "Foo and Bar"); note that this includes any necessary whitespace to
    # accommodate languages that do not use whitespace in such a pair construct.
    pair_sep = _(' and ')
    # Translators: This string separates items in a list (e.g.
    # "Foo, Bar, Baz, and Quux"); note that this includes any necessary
    # whitespace to accommodate languages that do not use whitespace in
    # such a list construct.
    list_sep = _(', ')
    # Translators: This string separates the final two items in a list (e.g.
    # "Foo, Bar and Baz"); note that this includes any necessary whitespace to
    # accommodate languages that do not use whitespace in such a list construct.
    final_list_sep = _(", and ")
    if len(values) == 0:
        return ''
    elif len(values) == 1:
        return values[0]
    elif len(values) == 2:
        return pair_sep.join(values)
    else:
        return u'{head}{final_list_sep}{tail}'.format(
            head=list_sep.join(values[:-1]),
            final_list_sep=final_list_sep,
            tail=values[-1]
        )


def _get_course_title(course_id):
    """
    Formatting helper.

    Transform an edX course id (e.g. "MITx/6.002x/2012_Fall") into a string
    suitable for use as a course title in digest notifications.

    >>> _get_course_title("MITx/6.002x/2012_Fall")
    '6.002x MITx'
    """
    return ' '.join(reversed(course_id.split('/')[:2]))


def _get_course_url(course_id):
    """
    Formatting helper.

    Generate a click-through url for a given edX course id.

    >>> _get_course_url("MITx/6.002x/2012_Fall").replace(
    ...        settings.LMS_URL_BASE, "URL_BASE")
    'URL_BASE/courses/MITx/6.002x/2012_Fall/'
    """
    return '{}/courses/{}/'.format(settings.LMS_URL_BASE, course_id)


def _get_thread_url(course_id, thread_id, commentable_id):
    """
    Formatting helper.

    Generate a click-through url for a specific discussion thread in an edX
    course.
    """
    thread_path = 'discussion/forum/{}/threads/{}'.format(commentable_id, thread_id)
    return _get_course_url(course_id) + thread_path


def _get_unsubscribe_url(username):
    """
    Formatting helper.

    Generate a click-through url to unsubscribe a user from digest notifications,
    using an encrypted token based on the username.
    """
    token = UsernameCipher.encrypt(username)
    return '{}/notification_prefs/unsubscribe/{}/'.format(settings.LMS_URL_BASE, token)


class Digest(object):
    def __init__(self, courses):
        self.courses = sorted(courses, key=lambda c: c.title.lower())

class DigestCourse(object):
    def __init__(self, course_id, threads):
        self.title = _get_course_title(course_id)
        self.url = _get_course_url(course_id)
        self.thread_count = len(threads) # not the same as len(self.threads), see below
        self.threads = sorted(threads, reverse=True, key=lambda t: t.dt)[:MAX_COURSE_THREADS]

class DigestThread(object):
    def __init__(self, thread_id, course_id, commentable_id, title, items):
        self.title = _trunc(strip_tags(title), THREAD_TITLE_MAXLEN)
        self.url = _get_thread_url(course_id, thread_id, commentable_id)
        self.items = sorted(items, reverse=True, key=lambda i: i.dt)[:MAX_THREAD_ITEMS]
 
    @property
    def dt(self):
        return max(item.dt for item in self.items)

class DigestItem(object):
    def __init__(self, body, author, dt):
        self.body = _trunc(strip_tags(body), THREAD_ITEM_MAXLEN)
        self.author = author
        self.dt = dt


@statsd.timed('notifier.digest_render.elapsed')
def render_digest(user, digest, title, description):
    """
    Generate HTML and plaintext renderings of digest material, suitable for
    emailing.


    `user` should be a dictionary with the following keys: "id", "name",
    "email" (all values should be nonempty strings).

    `digest` should be a Digest object as defined above in this module.

    `title` and `description` are brief strings to be displayed at the top
    of the email message.


    Returns two strings: (text_body, html_body).
    """
    logger.info("rendering email message: {user_id: %s}", user['id'])
    context = Context({
        'user': user,
        'digest': digest,
        'title': title,
        'description': description,
        'course_count': len(digest.courses),
        'course_names': _make_text_list([course.title for course in digest.courses]),
        'thread_count': sum(course.thread_count for course in digest.courses),
        'logo_image_url': "{}/static/images/header-logo.png".format(settings.LMS_URL_BASE),
        'unsubscribe_url': _get_unsubscribe_url(user['username'])
        })
    
    text = get_template('digest-email.txt').render(context)
    html = get_template('digest-email.html').render(context)

    return (text, html)

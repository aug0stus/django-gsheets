# coding:utf-8
"""
Ripped from https://raw.githubusercontent.com/trendmicro/backoff-python/master/backoff.py

Function decoration for backoff and retry

This module provides function decorators which can be used to wrap a
function such that it will be retried until some condition is met. It
is meant to be of use when accessing unreliable resources with the
potential for intermittent failures i.e. network resources and external
APIs. Somewhat more generally, it may also be of use for dynamically
polling resources for externally generated content.

For examples and full documentation see the README at
https://github.com/litl/backoff
"""
from __future__ import unicode_literals

import functools
import operator
import logging
import random
import time
import traceback
import sys

logger = logging.getLogger(__name__)


def expo(base=2, factor=1, max_value=None):
    """Generator for exponential decay.

    Args:
        base: The mathematical base of the exponentiation operation
        factor: Factor to multiply the exponentation by.
        max_value: The maximum value to yield. Once the value in the
             true exponential sequence exceeds this, the value
             of max_value will forever after be yielded.
    """
    n = 0
    while True:
        a = factor * base ** n
        if max_value is None or a < max_value:
            yield a
            n += 1
        else:
            yield max_value


def fibo(max_value=None):
    """Generator for fibonaccial decay.

    Args:
        max_value: The maximum value to yield. Once the value in the
             true fibonacci sequence exceeds this, the value
             of max_value will forever after be yielded.
    """
    a = 1
    b = 1
    while True:
        if max_value is None or a < max_value:
            yield a
            a, b = b, a + b
        else:
            yield max_value


def constant(interval=1):
    """Generator for constant intervals.

    Args:
        interval: The constant value in seconds to yield.
    """
    while True:
        yield interval


def random_jitter(value):
    """Jitter the value a random number of milliseconds.

    This adds up to 1 second of additional time to the original value.
    Prior to backoff version 1.2 this was the default jitter behavior.

    Args:
        value: The unadulterated backoff value.
    """
    return value + random.random()


def full_jitter(value):
    """Jitter the value across the full range (0 to value).

    This corresponds to the "Full Jitter" algorithm specified in the
    AWS blog's post on the performance of various jitter algorithms.
    (http://www.awsarchitectureblog.com/2015/03/backoff.html)

    Args:
        value: The unadulterated backoff value.
    """
    return random.uniform(0, value)


def backoff_on_predicate(wait_gen,
                 predicate=operator.not_,
                 max_tries=None,
                 jitter=full_jitter,
                 on_success=None,
                 on_backoff=None,
                 on_giveup=None,
                 **wait_gen_kwargs):
    """Returns decorator for backoff and retry triggered by predicate.

    Args:
        wait_gen: A generator yielding successive wait times in
            seconds.
        predicate: A function which when called on the return value of
            the target function will trigger backoff when considered
            truthily. If not specified, the default behavior is to
            backoff on falsey return values.
        max_tries: The maximum number of attempts to make before giving
            up. In the case of failure, the result of the last attempt
            will be returned.  The default value of None means their
            is no limit to the number of tries.
        jitter: A function of the value yielded by wait_gen returning
            the actual time to wait. This distributes wait times
            stochastically in order to avoid timing collisions across
            concurrent clients. Wait times are jittered by default
            using the full_jitter function. Jittering may be disabled
            altogether by passing jitter=None.
        on_success: Callable (or iterable of callables) with a unary
            signature to be called in the event of success. The
            parameter is a dict containing details about the invocation.
        on_backoff: Callable (or iterable of callables) with a unary
            signature to be called in the event of a backoff. The
            parameter is a dict containing details about the invocation.
        on_giveup: Callable (or iterable of callables) with a unary
            signature to be called in the event that max_tries
            is exceeded.  The parameter is a dict containing details
            about the invocation.
        **wait_gen_kwargs: Any additional keyword args specified will be
            passed to wait_gen when it is initialized.
    """
    success_hdlrs = _handlers(on_success)
    backoff_hdlrs = _handlers(on_backoff, _log_backoff)
    giveup_hdlrs = _handlers(on_giveup, _log_giveup)

    def decorate(target):

        @functools.wraps(target)
        def retry(*args, **kwargs):
            tries = 0

            wait = wait_gen(**wait_gen_kwargs)
            while True:
                tries += 1
                ret = target(*args, **kwargs)
                if predicate(ret):
                    if max_tries is not None and tries == max_tries:
                        for hdlr in giveup_hdlrs:
                            hdlr({'target': target,
                                  'args': args,
                                  'kwargs': kwargs,
                                  'tries': tries,
                                  'value': ret})
                        break

                    value = next(wait)
                    try:
                        if jitter is not None:
                            seconds = jitter(value)
                        else:
                            seconds = value
                    except TypeError:
                        # support deprecated nullary jitter function signature
                        # which returns a delta rather than a jittered value
                        seconds = value + jitter()

                    for hdlr in backoff_hdlrs:
                        hdlr({'target': target,
                              'args': args,
                              'kwargs': kwargs,
                              'tries': tries,
                              'value': ret,
                              'wait': seconds})

                    time.sleep(seconds)
                    continue
                else:
                    for hdlr in success_hdlrs:
                        hdlr({'target': target,
                              'args': args,
                              'kwargs': kwargs,
                              'tries': tries,
                              'value': ret})
                    break

            return ret

        return retry

    # Return a function which decorates a target with a retry loop.
    return decorate


def backoff_on_exception(wait_gen,
                         exception,
                         max_tries=None,
                         jitter=full_jitter,
                         on_success=None,
                         on_backoff=None,
                         on_giveup=None,
                         **wait_gen_kwargs):
    """Returns decorator for backoff and retry triggered by exception.

    Args:
        wait_gen: A generator yielding successive wait times in
            seconds.
        exception: An exception type (or tuple of types) which triggers
            backoff.
        max_tries: The maximum number of attempts to make before giving
            up. Once exhausted, the exception will be allowed to escape.
            The default value of None means their is no limit to the
            number of tries.
        jitter: A function of the value yielded by wait_gen returning
            the actual time to wait. This distributes wait times
            stochastically in order to avoid timing collisions across
            concurrent clients. Wait times are jittered by default
            using the full_jitter function. Jittering may be disabled
            altogether by passing jitter=None.
        on_success: Callable (or iterable of callables) with a unary
            signature to be called in the event of success. The
            parameter is a dict containing details about the invocation.
        on_backoff: Callable (or iterable of callables) with a unary
            signature to be called in the event of a backoff. The
            parameter is a dict containing details about the invocation.
        on_giveup: Callable (or iterable of callables) with a unary
            signature to be called in the event that max_tries
            is exceeded.  The parameter is a dict containing details
            about the invocation.
        **wait_gen_kwargs: Any additional keyword args specified will be
            passed to wait_gen when it is initialized.

    """
    success_hdlrs = _handlers(on_success)
    backoff_hdlrs = _handlers(on_backoff, _log_backoff)
    giveup_hdlrs = _handlers(on_giveup, _log_giveup)

    def decorate(target):

        @functools.wraps(target)
        def retry(*args, **kwargs):
            tries = 0
            wait = wait_gen(**wait_gen_kwargs)
            while True:
                try:
                    tries += 1
                    ret = target(*args, **kwargs)
                except exception:
                    if max_tries is not None and tries == max_tries:
                        for hdlr in giveup_hdlrs:
                            hdlr({'target': target,
                                  'args': args,
                                  'kwargs': kwargs,
                                  'tries': tries})
                        raise

                    value = next(wait)
                    try:
                        if jitter is not None:
                            seconds = jitter(value)
                        else:
                            seconds = value
                    except TypeError:
                        # support deprecated nullary jitter function signature
                        # which returns a delta rather than a jittered value
                        seconds = value + jitter()

                    for hdlr in backoff_hdlrs:
                        hdlr({'target': target,
                              'args': args,
                              'kwargs': kwargs,
                              'tries': tries,
                              'wait': seconds})

                    time.sleep(seconds)
                else:
                    for hdlr in success_hdlrs:
                        hdlr({'target': target,
                              'args': args,
                              'kwargs': kwargs,
                              'tries': tries})

                    return ret

        return retry

    # Return a function which decorates a target with a retry loop.
    return decorate


# Create default handler list from keyword argument
def _handlers(hdlr, default=None):
    defaults = [default] if default is not None else []

    if hdlr is None:
        return defaults

    if hasattr(hdlr, '__iter__'):
        return defaults + list(hdlr)

    return defaults + [hdlr]


# Formats a function invocation as a unicode string for logging.
def _invoc_repr(details):
    f, args, kwargs = details['target'], details['args'], details['kwargs']
    args_out = ", ".join("{0}".format(a) for a in args)
    if args and kwargs:
        args_out += ", "
    if kwargs:
        args_out += ", ".join("{0}={1}".format(k, v)
                              for k, v in kwargs.items())

    return "{0}({1})".format(f.__name__, args_out)


# Default backoff handler
def _log_backoff(details):
    msg = "Backing off {0} {1:.1f}s".format(_invoc_repr(details),
                                            details['wait'])

    exc_typ, exc, _ = sys.exc_info()
    if exc is not None:
        exc_fmt = traceback.format_exception_only(exc_typ, exc)[-1]
        msg = "{0} ({1})".format(msg, exc_fmt.rstrip("\n"))
        logger.error(msg)
    else:
        msg = "{0} ({1})".format(msg, details['value'])
        logger.info(msg)


# Default giveup handler
def _log_giveup(details):
    msg = "Giving up {0} after {1} tries".format(_invoc_repr(details),
                                                 details['tries'])

    exc_typ, exc, _ = sys.exc_info()
    if exc is not None:
        exc_fmt = traceback.format_exception_only(exc_typ, exc)[-1]
        msg = "{0} ({1})".format(msg, exc_fmt.rstrip("\n"))
    else:
        msg = "{0} ({1})".format(msg, details['value'])

    logger.error(msg)

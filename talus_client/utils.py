#!/usr/bin/env python
# encoding: utf-8

import colorama
import os
import random
import re
import requests
from tabulate import tabulate

colorama.init()

import talus_client.errors as errors

class Colors:
    HEADER = colorama.Fore.MAGENTA
    TITLE = colorama.Fore.BLUE
    OKBLUE = colorama.Fore.BLUE
    OKGREEN = colorama.Fore.GREEN
    WARNING = colorama.Fore.YELLOW
    FAIL = colorama.Fore.RED
    BLACK = colorama.Fore.BLACK

    DIM = colorama.Style.DIM
    BRIGHT = colorama.Style.BRIGHT
    ENDC = colorama.Style.RESET_ALL

def json_request(method, *args, **params):
    content_type = "application/json"

    if "data" in params and hasattr(params["data"], "content_type"):
        content_type = params["data"].content_type

    params.setdefault("headers", {}).setdefault("content-type", content_type)

    try:
        res = method(*args, **params)
    except requests.ConnectionError as e:
        raise errors.TalusApiError("Could not connect to {}".format(args[0]))
    except Exception as e:
        return None

    return res

def model_prompt(models, prompt_text, new_allowed=False):
    fields = []
    # in case it's an iterator
    models = list(models)
    for model in models:
        fields.append([len(fields)] + list(model))
    
    if new_allowed:
        new = [[0] + ["NEW" for x in xrange(len(fields[0]))]]
        for field in fields:
            field[0] = field[0] + 1
        fields = new + fields
    
    idx = idx_prompt(fields, prompt_text, headers=models[0].headers())
    if idx is None:
        return None

    if new_allowed:
        if idx == 0:
            return "NEW"
        else:
            return models[idx-1]
    else:
        return models[idx]

def idx_prompt(fields, prompt_text, headers=None, colors=True):
    if colors:
        for field in fields:
            field[0] = Colors.FAIL + str(field[0]) + Colors.ENDC

        if headers is not None:
            for x in xrange(len(headers)):
                headers[x] = Colors.BRIGHT + Colors.BLACK + headers[x] + Colors.ENDC

    print(tabulate(fields, headers=headers))
    
    if colors:
        prompt_text = "\n" + Colors.WARNING + prompt_text + " (idx or q):" + Colors.ENDC + " "
    else:
        prompt_text = "\n" + prompt_text + " (idx or q): "

    answer = raw_input(prompt_text)
    idx = None
    while True:
        # bail
        if answer == "q":
            return
        idx = None
        try:
            idx = int(answer)
        except:
            answer = raw_input(Colors.WARNING + "invalid response, try again (idx or q):" + Colors.ENDC + " ")
            continue

        if 0 <= idx < len(fields):
            break
        else:
            answer = raw_input(Colors.WARNING + "idx out of range, try again (idx or q):" + Colors.ENDC + " ")
    
    return idx

with open(os.path.join(os.path.dirname(__file__), "adjectives.txt"), "r") as f:
    ADJECTIVES = f.read().split("\n")
with open(os.path.join(os.path.dirname(__file__), "nouns.txt"), "r") as f:
    NOUNS = f.read().split("\n")

def rand_words(adjectives=1, nouns=1):
    adj = [random.choice(ADJECTIVES) for x in xrange(adjectives)]
    nouns = [random.choice(NOUNS) for x in xrange(nouns)]

    return adj + nouns

def strip_color(data):
    return re.sub(r'\x1b[^m]*m', '', data)

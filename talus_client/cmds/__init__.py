#!/usr/bin/env python
# encoding: utf-8

import argparse
import arrow
import cmd
import datetime
import glob
import json
import inspect
import os
import re
import readline
import shlex
import sys
import textwrap
import types

import talus_client.api
import talus_client.errors as errors
import talus_client.utils as utils
from talus_client.utils import Colors

ModelCmd = None

ENABLED_COMMANDS = []

class TalusMetaClass(type):
    def __init__(cls, name, bases, namespace):
        global ENABLED_COMMANDS
        super(TalusMetaClass, cls).__init__(name, bases, namespace)

        if cls.__name__ in ["TalusCmdBase"]:
            return

        ENABLED_COMMANDS.append(cls)

class TalusCmdBase(object,cmd.Cmd):
    __metaclass__ = TalusMetaClass

    # to be overridden by inheriting classes
    command_name = ""
    ansi_escape = re.compile(r'\x1b[^m]*m')

    def __init__(self, talus_host=None, client=None, user=None):
        """Create a new TalusCmdBase

        :talus_host: The root of the talus web app (e.g. http://localhost:8001 if the api is at http://localhost:8001/api)
        """
        global ModelCmd
        from talus_client.param_model import ModelCmd as MC
        ModelCmd = MC

        cmd.Cmd.__init__(self, "\t")

        self.one_shot = False

        self._last_was_keyboard = False
        self._talus_host = talus_host
        self._talus_client = client
        self._talus_user = user
        if self._talus_host is not None and self._talus_client is None:
            self._talus_client = talus_client.api.TalusClient(self._talus_host, user=self._talus_user)

    def _plain_text(self, text):
        """Return a plain version of the text with control chars removed
        """
        return self.ansi_escape.sub('', text)
    
    def _nice_name(self, model, attr, show_id=True):
        if "name" in model._fields[attr].value:
            if show_id:
                return "{} ({})".format(model._fields[attr]["name"], model._fields[attr]["id"])
            else:
                return "{}".format(model._fields[attr]["name"])
        else:
            return getattr(model, attr)
    
    def _resolve_one_model(self, id_or_name, model, search, sort="-timestamps.created", default_id_search=None):
        if default_id_search is None:
            default_id_search = ["id", "name"]

        if id_or_name is not None and not id_or_name.startswith("+"):
            for default_compare in default_id_search:
                res = model.find_one(**{default_compare:id_or_name})
                if res is not None:
                    return res
            return None

        if id_or_name is None:
            skip = 0
        else:
            if not re.match(r'^\+\d+$', id_or_name):
                raise errors.TalusApiError("Git-like referencing must be a plus sign followed by digits")
            skip = int(id_or_name.replace("+", "")) - 1
        search["skip"] = skip
        search["num"] = 1
        search["sort"] = sort
        return model.find_one(**search)
    
    def _search_terms(self, parts, key_remap=None, user_default_filter=True, out_leftover=None, no_hex_keys=None):
        """Return a dictionary of search terms"""

        if no_hex_keys is None:
            no_hex_keys = []
        search = {}
        key = None
        if key_remap is None:
            key_remap = {}

        key_map = {
            "status": "status.name"
        }
        key_map.update(key_remap)
        found_all = False

        for item in parts:
            if key is None:
                if not item.startswith("--"):
                    if out_leftover is not None:
                        out_leftover.append(item)
                        continue
                    else:
                        raise errors.TalusApiError("args must be alternating search item/value pairs!")
                item = item[2:].replace("-", "_")
                key = item
                if key == "all":
                    found_all = True
                    key = None
                    continue

                if key in key_map:
                    key = key_map[key]

                if key.endswith("__type") or key.endswith(".type"):
                    key += "_"
                elif key.endswith("__re"):
                    key = key.replace("__re", ".$regex")

            elif key is not None:
                # hex conversion
                if re.match(r'^0x[0-9a-f]+$', item, re.IGNORECASE) is not None and key.split("__")[0] not in no_hex_keys:
                    item = int(item, 16)

                if key in search and not isinstance(search[key], list):
                    search[key] = [search[key]]

                if key in search and isinstance(search[key], list):
                    search[key].append(item)
                else:
                    search[key] = item
                self.out("searching for {} = {}".format(key, item))

                # reset this
                key = None

        if user_default_filter and not found_all and self._talus_user is not None:
            # default filter by username tag
            self.out("default filtering by username (searching for tags = {})".format(self._talus_user))
            self.out("use --all to view all models")

            if "tags" in search and not isinstance(search["tags"], list):
                search["tags"] = [search["tags"]]

            if "tags" in search and isinstance(search["tags"], list):
                search["tags"].append(self._talus_user)
            else:
                search["tags"] = self._talus_user

        if out_leftover is not None and key is not None:
            out_leftover.append(key)

        return search
    
    def _actual_date(self, epoch):
        return datetime.datetime.fromtimestamp(epoch).strftime("%Y-%m-%d %H:%M:%S")
    
    def _rel_date(self, epoch):
        return arrow.get(epoch).humanize()
    
    def _prep_model(self, model):
        if hasattr(model, "tags") and self._talus_user is not None and self._talus_user not in model.tags:
            model.tags.append(self._talus_user)
    
    def _make_model_cmd(self, model, prompt_part="create"):
        res = ModelCmd(model, self._talus_host, self._talus_client)
        res.prompt = self.prompt[:-2] + ":" + prompt_part + "> "
        res._root = self._root
        return res
    
    def _go_interactive(self, args):
        return ("--shell" in args or (len(args) == 0 and not self._root.one_shot))
    
    def ask(self, msg):
        msg = Colors.WARNING + msg + Colors.ENDC
        return raw_input(msg)

    def ok(self, msg):
        """
        Print the message with a success/ok color
        """
        msg = u"\n".join(Colors.OKGREEN + u"{}{}".format(u"[.]  ", line) + Colors.ENDC for line in unicode(msg).split("\n"))
        print(msg)

    def out(self, msg, raw=False):
        """
        Print the message with standard formatting
        """
        pre = Colors.OKBLUE + "[+]" + Colors.ENDC + "  "
        if raw:
            pre = "    "
        msg = u"\n".join(u"{}{}".format(pre, line) for line in unicode(msg).split("\n"))
        print(msg)

    def warn(self, msg):
        """
        Print an error message
        """
        # TODO colors?
        msg = u"\n".join(Colors.FAIL + u"[!]  {}".format(line) + Colors.ENDC for line in unicode(msg).split("\n"))
        print(msg)

    def err(self, msg):
        """
        Print an error message
        """
        # TODO colors?
        msg = u"\n".join(Colors.FAIL + u"[E]  {}".format(line) + Colors.ENDC for line in unicode(msg).split("\n"))
        print(msg)

    @property
    def prompt(self):
        caller_name = inspect.stack()[1][3]
        if caller_name == "cmdloop":
            return Colors.HEADER + self._prompt + Colors.ENDC

        return self._prompt
    
    @prompt.setter
    def prompt(self, value):
        self._prompt = value
        return self._prompt

    def emptyline(self):
        """don't repeat the last successful command"""
        pass
    
    def do_up(self, args):
        """Quit the current processor (move up a level)"""
        return True

    def do_quit(self, args):
        """Quit the program"""
        return True
    do_exit = do_quit
    do_exit.__doc__ = do_quit.__doc__
    
    def cmdloop(self, *args, **kwargs):
        try:
            return cmd.Cmd.cmdloop(self, *args, **kwargs)
        except KeyboardInterrupt as e:
            self.err("cancelled")
            return True
    
    def onecmd(self, *args, **kwargs):
        try:
            return cmd.Cmd.onecmd(self, *args, **kwargs)
        except talus_client.errors.TalusApiError as e:
            self.err(e.message)
        except KeyboardInterrupt as e:
            if not self._last_was_keyboard:
                self.err("cancelled")
            else:
                self.err("if you want to quit, use the 'quit' command")
            self._last_was_keyboard = True
        # raised by argparse when args aren't correct
        except SystemExit as e:
            pass
        else:
            # no KeyboardInterrupts happened
            self._last_was_keyboard = False

    def default(self, line):
        funcs = filter(lambda x: x.startswith("do_"), dir(self))
        parts = line.split()
        first_param = parts[0]
        matches = filter(lambda x: x.startswith("do_" + first_param), funcs)
        if len(matches) > 1:
            self.warn("ambiguous command, matching commands:")
            for match in matches:
                print("    " + match.replace("do_", ""))
            return

        elif len(matches) == 1:
            func = getattr(self, matches[0])
            return func(" ".join(parts[1:]))

        self.err("Unknown command. Try the 'help' command.")
    
    def completedefault(self, text, line, begidx, endidx):
        funcs = filter(lambda x: x.startswith("do_"), dir(self))
        res = filter(lambda x: x.startswith(text), funcs)
        return res
    
    @classmethod
    def get_command_helps(cls):
        """Look for methods in this class starting with do_.

        :returns: A dict of commands and their help values. E.g. ``{"list": "List all the images"}``
        """
        res = {}
        regex = re.compile(r'^do_(.*)$')
        for name in dir(cls):
            match = regex.match(name)
            if match is not None:
                cmd = match.group(1)
                prop = getattr(cls, name, None)
                doc = getattr(prop, "__doc__", None)
                if doc is not None:
                    lines = doc.split("\n")
                    res[cmd] = lines[0].lstrip() + textwrap.dedent("\n".join(lines[1:]).expandtabs(4))
        return res
    
    @classmethod
    def get_help(cls, args=None, abbrev=False, examples=False):
        args = "" if args is None else args
        cmd = None
        cmd_specific = (len(args) > 0)

        cmd_helps = ""
        if not cmd_specific:
            cmd_helps += "\n{name}\n{under}\n".format(
                name=cls.command_name,
                under=("-"*len(cls.command_name))
            )
        else:
            cmd = args.split(" ")[0]

        for subcmd_name,subcmd_help in cls.get_command_helps().iteritems():
            if cmd_specific and subcmd_name != cmd:
                continue

            if not examples and "\nExamples:\n" in subcmd_help:
                subcmd_help,_ = subcmd_help.split("\nExamples:\n")

            lines = subcmd_help.split("\n")
            first_line = lines[0].lstrip()

            label_start = "\n{:>10}   -   ".format(subcmd_name)
            spaces = " " * len(label_start)

            label_line = label_start + first_line
            cmd_helps += "\n".join(textwrap.wrap(
                label_line,
                subsequent_indent=spaces
            ))

            if len(lines) > 2 and not abbrev:
                cmd_helps += "\n\n" + "\n".join(spaces + x for x in lines[1:])

            cmd_helps += "\n"
        
        return cmd_helps
    
    def do_help(self, args):
        examples = (len(args) > 0)

        print(self.get_help(args=args, examples=examples))
    
    # -----------------------------------

    def _argparser(self):
        # TODO make this a loop and find the first do_XXXX function in
        # the current callstack?
        caller_name = inspect.stack()[1][3]

        if self.one_shot:
            return argparse.ArgumentParser(self.command_name + " " + caller_name.replace("do_", ""))
        else:
            return argparse.ArgumentParser(caller_name.replace("do_", ""))

class TalusCmd(TalusCmdBase):
    """The main talus command. This is what is invoked when dropping
    into a shell or when run from the command line"""

    command_name = "<ROOT>"

    def __init__(self, talus_host=None, client=None, one_shot=False, user=None):
        """Initialize the Talus command object
        :one_shot: True if only one command is to be processed (cmd-line args, no shell, etc)
        """
        super(TalusCmd, self).__init__(talus_host=talus_host, client=client, user=user)

        self.prompt = "talus> "

        self.one_shot = one_shot

# auto-import all defined commands in talus/cmds/*.py

this_dir = os.path.dirname(__file__)
for filename in glob.glob(os.path.join(this_dir, "*.py")):
    basename = os.path.basename(filename)
    if basename == "__init__.py":
        continue
    mod_name = basename.replace(".py", "")
    mod_base = __import__("talus_client.cmds", globals(), locals(), fromlist=[mod_name])
    mod = getattr(mod_base, mod_name)

def make_cmd_handler(cls):
    def _handle_command(self, args):
        processor = cls(talus_host=self._talus_host, client=self._talus_client, user=self._talus_user)
        processor._root = self
        processor.prompt = "talus:" + processor.command_name + "> "
        if self.one_shot or len(args) > 0:
            processor.one_shot = True
            processor.onecmd(args)
        else:
            processor.cmdloop()

    return _handle_command

def define_root_commands():
    for cls in ENABLED_COMMANDS:
        if cls.command_name == "" or cls == TalusCmd:
            continue

        handler = make_cmd_handler(cls)

        # the baseclass cmd.Cmd always defines a do_help, so we need to check if it's
        # redefined in the specific subclass
        if "do_help" in cls.__dict__:
            handler.__doc__ = cls.do_help.__doc__
        else:
            handler.__doc__ = cls.__doc__

        setattr(TalusCmd, "do_" + cls.command_name, handler)
define_root_commands()

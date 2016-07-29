#!/usr/bin/env python
# encoding: utf-8

import argparse
import cmd
from collections import deque
import json
import math
import md5
import os
import shlex
import sys
from tabulate import tabulate
import textwrap

from talus_client.cmds import TalusCmdBase
import talus_client.api
import talus_client.errors as errors
from talus_client.models import *
import talus_client.utils as utils
import colorama

class CrashesCmd(TalusCmdBase):
    """The talus crash command processor
    """

    command_name = "crash"

    def do_list(self, args):
        """List crashes in talus. Fields to be searched for must
        be turned into parameter format (E.g. ``--search-item "some value"`` format would search
        for a result with the field ``search_item`` equaling ``some value``).

        Examples:
            crash list --search-item "search value" [--search-item2 "search value2" ...]
            crash list --registers.eip 0x41414141
            crash list --all --tags browser_fuzzing
            rash list --exploitability EXPLOITABLE
            crash list --\$where "(this.data.registers.eax + 0x59816867) == this.data.registers.eip"
        """
        parts = shlex.split(args)

        all_mine = False
        if "--all-mine" in parts:
            parts.remove("--all-mine")
            all_mine = True

        search = self._search_terms(parts, no_hex_keys=["hash_major", "hash_minor", "hash"])

        root_level_items = ["created", "tool", "tags", "job", "tool", "$where", "sort", "num", "skip"]
        new_search = {}
        new_search["type"] = "crash"
        for k,v in search.iteritems():
            if k in root_level_items:
                new_search[k] = v
            else:
                new_search["data." + k] = v
        search = new_search

        if "sort" not in search:
            search["sort"] = "-created"

        if "--all" not in parts and not all_mine and "num" not in search:
            search["num"] = 20
            self.out("showing first 20 results")

        rows = []
        headers = ["id", "task", "tags", "created", "exploitability", "hash", "instr", "registers"]
        added_regs = False

        for crash in self._talus_client.result_iter(**search):
            # yes, define this in here so it's the same for every crash result
            colors = deque([
                colorama.Fore.MAGENTA,
                colorama.Fore.CYAN,
                colorama.Fore.YELLOW,
                colorama.Fore.GREEN,
                colorama.Fore.BLUE,
                colorama.Fore.RED,
            ])

            crashing_instr = None
            asm = crash.data.setdefault("disassembly", ["?"])
            for x in asm:
                if "->" in x:
                    match = re.match(r'^-+>(.*$)', x)
                    crashing_instr = match.group(1).strip()
            # if we don't find the arrow, we'll say it's the last instruction in the
            # list
            if crashing_instr is None:
                crashing_instr = x

            crashing_instr = re.sub(r'^([a-f0-9]+\s)*(.*)$', '\\2', crashing_instr).strip()
            crashing_instr = re.sub(r'\s+', " ", crashing_instr)

            registers = crash.data.setdefault("registers", {})
            reg_list = []
            reg_colors = {}
            for reg,val in registers.iteritems():
                reg = reg.lower()

                pad_len = 8
                # x64
                if reg.startswith("r"):
                    pad_len = 16

                # if we want it 0-padded, do "{:0" + str(pad_len) + "x}"
                fmt = "{:" + str(pad_len) + "x}"

                color = colors.popleft()
                reg_colors[reg] = color

                # YES, always cycle the colors so similar architecture will always
                # have matching colors for the registers. Just conditionally add
                # the registers to the output
                if reg in crashing_instr:
                    reg_list.append("{}:{}{}{}".format(
                        reg,
                        color,
                        fmt.format(val),
                        colorama.Style.RESET_ALL
                    ))
                colors.append(color)

            for reg,color in reg_colors.iteritems():
                crashing_instr = re.sub(r"\b" + reg + r"\b", color + reg + colorama.Style.RESET_ALL, crashing_instr)

            rows.append([
                crash.id,
                crash.tool,
                ",".join(crash.tags),
                self._rel_date(crash.created),
                crash.data.setdefault("exploitability", "?"),
                "{}:{}".format(crash.data.setdefault("hash_major", "?"), crash.data.setdefault("hash_minor", "?")),
                crashing_instr,
                " ".join(reg_list)
            ])

        print(tabulate(rows, headers=headers))
    
    def do_info(self, args, return_string=False, crash=None, show_details=False):
        """List detailed information about the crash.

        Git-like syntax can also be used here to refer to the most recently created
        crash result. E.g. the command below will show info about the 2nd most recent crash:

            crash info +2

        Search information can also be used. If git-like syntax is omitted, only
        the first entry returned from the database will be displayed.

            crash info --all --registers.eip 0x41414141 --sort registers.eax +1

        The example above will show information about the crash with the lowest eax value
        (+2 would show the 2nd lowest) that has an eip 0f 0x41414141.  Omitting --all will
        cause the search to be performed only among _your_ crashes.

        To view _all_ of the details about a crash, add the --details flag.
        """
        if crash is None:
            if args.strip() == "":
                raise errors.TalusApiError("you must provide a name/id/git-thing of a crash to show info about it")

            parts = shlex.split(args)

            if "--details" in parts:
                parts.remove("--details")
                show_details = True

            leftover = []
            crash_id_or_name = None
            search = self._search_terms(parts, out_leftover=leftover, no_hex_keys=["hash_major", "hash_minor", "hash"])

            root_level_items = ["created", "tags", "job", "tool", "$where", "sort", "num"]
            new_search = {}
            new_search["type"] = "crash"
            for k,v in search.iteritems():
                if k in root_level_items:
                    new_search[k] = v
                else:
                    new_search["data." + k] = v
            search = new_search

            if len(leftover) > 0:
                crash_id_or_name = leftover[0]

            crash = self._resolve_one_model(crash_id_or_name, Result, search, sort="-created")
            if crash is None:
                raise errors.TalusApiError("could not find a crash with that id/search")

        crashing_instr = None
        asm = crash.data.setdefault("disassembly", ["?"])
        for x in asm:
            if "->" in x:
                match = re.match(r'^-+>(.*$)', x)
                crashing_instr = match.group(1).strip()
        # if we don't find the arrow, we'll say it's the last instruction in the
        # list
        if crashing_instr is None:
            crashing_instr = x

        crashing_instr = re.sub(r'^([a-f0-9]+\s)*(.*)$', '\\2', crashing_instr).strip()
        crashing_instr = re.sub(r'\s+', " ", crashing_instr)

        colors = deque([
            colorama.Fore.MAGENTA,
            colorama.Fore.CYAN,
            colorama.Fore.YELLOW,
            colorama.Fore.GREEN,
            colorama.Fore.BLUE,
            colorama.Fore.RED,
        ])

        reg_colors = {}
        reg_rows = []
        reg_rows_no_color = []
        registers = crash.data.setdefault("registers", {})
        for reg,val in registers.iteritems():
            reg = reg.lower()
            color = colors.popleft()
            reg_colors[reg] = color
            reg_rows.append([reg, color + "{:8x}".format(val) + colorama.Style.RESET_ALL])
            reg_rows_no_color.append([reg, "{:8x}".format(val)])
            colors.append(color)

        split = int(math.ceil(len(reg_rows)/2.0))
        table1 = tabulate(reg_rows[:split]).split("\n")
        table1_no_color = tabulate(reg_rows_no_color[:split]).split("\n")
        table2 = tabulate(reg_rows[split:]).split("\n")

        longest_t1 = max(len(x) for x in table1_no_color)

        if len(table2) == 0:
            reg_lines = table1
        else:
            reg_lines = []
            for x in xrange(len(table1)):
                if x >= len(table2):
                    reg_lines.append(table1[x])
                else:
                    fmt_string = "{:" + str(longest_t1) + "}  |  {}"
                    reg_lines.append(fmt_string.format(table1[x], table2[x]))

        for reg,color in reg_colors.iteritems():
            crashing_instr = re.sub(r"\b" + reg + r"\b", color + reg + colorama.Style.RESET_ALL, crashing_instr)

        indent = "                  "


        arrow = ""
        asm_text = crash.data.setdefault("disassembly", [])
        for line in asm_text:
            if "->" in line:
                arrow = line.split()[0]

        asm_rows = []
        asm_rows_no_color = []
        arrow_indent = " " * len(arrow)
        for line in crash.data.setdefault("disassembly", []):
            line = line.strip()
            line = re.sub(r'\s+', " ", line)
            if not line.startswith(arrow):
                line = " " * len(arrow) + line

            line_no_color = line
            for reg,color in reg_colors.iteritems():
                line = re.sub(r"\b" + reg + r"\b", color + reg + colorama.Style.RESET_ALL, line)

            has_arrow = (arrow in line)
            line = line.replace(arrow, "")
            line_no_color = line_no_color.replace(arrow, "")

            match = re.match(r'^\s+([a-f0-9]+)\s+([a-f0-9]+)\s+(.*)$', line)
            no_color_match = re.match(r'^\s+([a-f0-9]+)\s+([a-f0-9]+)\s+(.*)$', line_no_color)
            if match is None:
                match2 = re.match(r'^\s+([a-f0-9]+)\s+(.*)$', line)
                no_color_match2 = re.match(r'^\s+([a-f0-9]+)\s+(.*)$', line_no_color)
                if match2 is None:
                    asm_rows.append(["", "", "", line])
                    asm_rows_no_color.append(["", "", "", line_no_color])
                else:
                    asm_rows.append(["-->" if has_arrow else "", "", match2.group(1), match2.group(2)])
                    asm_rows_no_color.append(["-->" if has_arrow else "", "", no_color_match2.group(1), no_color_match2.group(2)])
            else:
                asm_rows.append(["-->" if has_arrow else "", match.group(1), match.group(2), match.group(3)])
                asm_rows_no_color.append(["-->" if has_arrow else "", no_color_match.group(1), no_color_match.group(2), no_color_match.group(3)])

        table1 = asm_lines = tabulate(asm_rows).split("\n")
        table1_no_color = tabulate(asm_rows_no_color).split("\n")
        table2 = reg_lines
        
        longest_t1 = max(len(x) for x in table1_no_color)
        asm_and_regs = []

        if len(table2) == 0:
            asm_and_regs = table1
        else:
            asm_and_regs = []
            for x in xrange(len(table1)):
                if x >= len(table2):
                    asm_and_regs.append(table1[x])
                else:
                    no_color_diff = len(table1[x]) - len(table1_no_color[x])
                    fmt_string = "{:" + str(longest_t1 + no_color_diff) + "}  |  {}"
                    asm_and_regs.append(fmt_string.format(table1[x], table2[x]))

        details = ""
        if show_details:
            if isinstance(crash.data.setdefault("backtrace", ""), list):
                crash.data["backtrace"] = "\n".join(crash.data["backtrace"])

            detail_indent = " " * 4

            details += """
Stack: \n{stack}
Loaded Modules: \n{loaded_mods}
Backtrace: \n{backtrace}
Exploitability Details: \n{exploit_details}
            """.format(
                stack            = "\n".join(detail_indent + x for x in crash.data.setdefault("stack", "").split("\n")),
                loaded_mods        = "\n".join(detail_indent + x for x in crash.data.setdefault("loaded_modules", "").split("\n")),
                backtrace        = "\n".join(detail_indent + x for x in crash.data.setdefault("backtrace", "").split("\n")),
                exploit_details    = "\n".join(detail_indent + x for x in crash.data.setdefault("exploitability_details", "").split("\n")),
            )

        res = """
              ID: {id}
            Tags: {tags}
             Job: {job}
  Exploitability: {expl}
Hash Major/Minor: {hash_major} {hash_minor}
     Crash Instr: {crash_instr}
    Crash Module: {crash_module}
  Exception Code: {exception_code:8x}

{asm_and_regs}{details}
        """.format(
            id                = crash.id,
            tags            = ",".join(crash.tags),
            job                = self._nice_name(crash, "job"),
            expl            = crash.data.setdefault("exploitability", "None"),
            hash_major        = crash.data.setdefault("hash_major", "None"),
            hash_minor        = crash.data.setdefault("hash_minor", "None"),
            crash_instr        = crashing_instr,
            crash_module    = crash.data.setdefault("crash_module", ""),
            exception_code    = crash.data.setdefault("exception_code", 0),
            reg_tables        = "\n".join(indent + x for x in reg_lines),
            asm_and_regs    = "\n".join("    " + x for x in asm_and_regs),
            details            = details,
        )

        if not return_string:
            print(res)
        else:
            return res
    
    def do_export(self, args):
        """Export crash information to the target directory. Crashes are identified using
        git-like syntax, ids, and/or search queries, as with the info commands:

            crash export --tags IE +2

        The above command will export the 2nd most recent crash (+2) that belongs to you and
        contains the tag "IE".

        By default crashes will be saved into the current working directory. Use the --dest
        argument to specify a different output directory:

            crash export +1 --all --tags adobe --dest adobe_crashes

        The more complicated example below will search among all crashes (--all, vs only
        those tagged with your username) for ones that have an exploitability category of
        EXPLOITABLE and crashing module of libxml. The second crash (+2) will be chosen
        after sorting by data.registers.eax

            crash export --all --exploitability EXPLOITABLE --crashing_module libxml --sort data.registers.eax +2
        """
        if args.strip() == "":
            raise errors.TalusApiError("you must provide a name/id/git-thing of a crash to export it")

        parts = shlex.split(args)

        leftover = []
        crash_id_or_name = None
        search = self._search_terms(parts, out_leftover=leftover, no_hex_keys=["hash_major", "hash_minor", "hash"])

        root_level_items = ["created", "tags", "job", "tool", "$where", "sort", "num", "dest"]
        new_search = {}
        new_search["type"] = "crash"
        for k,v in search.iteritems():
            if k in root_level_items:
                new_search[k] = v
            else:
                new_search["data." + k] = v
        search = new_search

        dest_dir = search.setdefault("dest", os.getcwd())
        dest_dir = os.path.expanduser(dest_dir)
        del search["dest"]

        if len(leftover) > 0:
            crash_id_or_name = leftover[0]

        crash = self._resolve_one_model(crash_id_or_name, Result, search, sort="-created")
        if crash is None:
            raise errors.TalusApiError("could not find a crash with that id/search")

        self.ok("exporting crash {} from job {}".format(
            crash.id,
            self._nice_name(crash, "job"),
            crash.tags
        ))

        first_num = int(crash.data["hash_major"], 16)
        second_num = int(crash.data["hash_minor"], 16)
        adj = utils.ADJECTIVES[first_num % len(utils.ADJECTIVES)]
        noun = utils.NOUNS[second_num % len(utils.NOUNS)]

        dest_name = "{}_{}_{}".format(adj, noun, crash.id)
        dest_path = os.path.join(dest_dir, dest_name)
        self.ok("saving to {}".format(dest_path))

        if os.path.exists(dest_path):
            self.warn("export path ({}) already exists! not gonna overwrite it, bailing".format(dest_path))
            return

        os.makedirs(dest_path)

        file_path = os.path.join(dest_path, "crash.json")
        self.out(file_path)
        with open(file_path, "wb") as f:
            f.write(json.dumps(crash._filtered_fields(), indent=4, separators=(",", ": ")).encode("utf-8"))

        file_path = os.path.join(dest_path, "crash.txt")
        self.out(file_path)
        with open(file_path, "wb") as f:
            txt_info = self.do_info("", return_string=True, crash=crash, show_details=True)
            txt_info = utils.strip_color(txt_info)
            f.write(txt_info.encode("utf-8"))

        for file_id in crash.data.setdefault("repro", []):
            fname,data = self._talus_client.corpus_get(file_id)
            if fname is None:
                fname = file_id

            file_path = os.path.join(dest_path, "repro", fname)
            if not os.path.exists(os.path.dirname(file_path)):
                os.makedirs(os.path.dirname(file_path))

            self.out(file_path)
            with open(file_path, "wb") as f:
                f.write(data.encode("utf-8"))

        self.ok("done exporting crash")

#!/usr/bin/env python
# encoding: utf-8

import argparse
import cmd
import os
import shlex
import sys
from tabulate import tabulate
import textwrap

from talus_client.cmds import TalusCmdBase
import talus_client.api
import talus_client.errors as errors
from talus_client.models import *

class ResultCmd(TalusCmdBase):
    """The talus result command processor
    """

    command_name = "result"

    def do_list(self, args):
        """List results in talus for a specific job. Fields to be searched for must
        be turned into parameter format (E.g. ``--search-item "some value"`` format would search
        for a result with the field ``search_item`` equaling ``some value``).

        result list --search-item "search value" [--search-item2 "search value2" ...]
        """
        parts = shlex.split(args)

        all_mine = False
        if "--all-mine" in parts:
            parts.remove("--all-mine")
            all_mine = True

        search = self._search_terms(parts)

        if "sort" not in search:
            search["sort"] = "-created"

        if "--all" not in parts and not all_mine and "num" not in search:
            search["num"] = 20
            self.out("showing first 20 results")

        print(tabulate(self._talus_client.result_iter(**search), headers=Result.headers()))

    def do_export(self, args):
        """Export raw result json to the target file. Results are identified  using
        git-like syntax, ids, and/or search queries, as with the info commands:

            result export --tags IE +2

        The above command will export the 2nd most recent result (+2) that belongs to you and
        contains the tag "IE".

        By default results will be saved into the current working directory using the
        result's database ID as the filename (XXXXXX.json). Use the --dest
        argument to specify a different output file

            result export +1 --tags adobe --dest some_result.json

        The more complicated example below will search among all crashes (--all, vs only
        those tagged with your username) for ones that have an exploitability category of
        EXPLOITABLE and crashing module of libxml. The second crash (+2) will be chosen
        after sorting by data.registers.eax

            result export --all --exploitability EXPLOITABLE --crashing_module libxml --sort data.registers.eax +2
        """
        if args.strip() == "":
            raise errors.TalusApiError("you must provide a name/id/git-thing of a result to export it")

        parts = shlex.split(args)

        leftover = []
        crash_id_or_name = None
        search = self._search_terms(parts, out_leftover=leftover, no_hex_keys=["hash_major", "hash_minor", "hash"])

        root_level_items = ["created", "tags", "job", "tool", "$where", "sort", "num", "dest"]
        new_search = {}
        for k,v in search.iteritems():
            if k.split("__")[0] in root_level_items:
                new_search[k] = v
            else:
                new_search["data." + k] = v
        search = new_search

        dest_path = search.get("dest", None)
        if "dest" in search:
            del search["dest"]

        if len(leftover) > 0:
            result_id_or_name = leftover[0]

        result = self._resolve_one_model(result_id_or_name, Result, search, sort="-created")
        if result is None:
            raise errors.TalusApiError("could not find a result with that id/search")

        self.ok("exporting result {} from job {}".format(
            result.id,
            self._nice_name(result, "job"),
            result.tags
        ))

        if dest_path is None:
            dest_path = "{}.json".format(result.id)

        self.ok("saving to {}".format(dest_path))

        if os.path.exists(dest_path):
            self.warn("export path ({}) already exists! not gonna overwrite it, bailing".format(dest_path))
            return

        with open(dest_path, "wb") as f:
            f.write(result.json().encode("utf-8"))

        self.ok("done exporting result")

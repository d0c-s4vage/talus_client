#!/usr/bin/env python
# encoding: utf-8

import argparse
import cmd
from collections import deque
import os
import shlex
import sys
from tabulate import tabulate
import time

from talus_client.cmds import TalusCmdBase
import talus_client.api
import talus_client.errors as errors
from talus_client.models import Image,Field
from talus_client.utils import Colors

class ImageCmd(TalusCmdBase):
    """The Talus images command processor
    """

    command_name = "image"

    def do_list(self, args):
        """List existing images in Talus
        
        image list

        Examples:

        List all images in Talus:

            image list
        """
        parts = shlex.split(args)
        search = self._search_terms(parts, user_default_filter=False)

        if "sort" not in search:
            search["sort"] = "timestamps.created"

        if "--all" not in parts and "num" not in search:
            search["num"] = 20
            self.out("showing first 20 results, use --all to see everything")

        headers = [
            "id",
            "name",
            "status",
            "base_image",
            "tags",
        ]
        fields = []
        for image in self._talus_client.image_iter(**search):
            status = image.status["name"]
            if "vnc" in image.status:
                status = image.status["vnc"]["vnc"]["uri"]

            fields.append([
                image.id,
                image.name,
                status,
                self._nice_name(image, "base_image", show_id=False) if image.base_image is not None else None,
                ",".join(image.tags)
            ])

        print(tabulate(fields, headers=headers))

    def do_tree(self, args):
        """Show the entire snapshot tree
        """
        parts = shlex.split(args)
        search = self._search_terms(parts, user_default_filter=False)
        search.setdefault("base_image", "null")

        base_images = Image.objects(**search)
        self._print_snapshot_tree(base_images)

    def do_info(self, args):
        """List detailed information about an image

        info ID_OR_NAME

        Examples:

        List info about the image named "Win 7 Pro"

            image info "Win 7 Pro"
        """
        if args.strip() == "":
            raise errors.TalusApiError("you must provide a name/id of an image to show info about it")

        parts = shlex.split(args)

        all_mine = False
        # this is the default, so just remove this flag and ignore it
        if "--all-mine" in parts:
            parts.remove("--all-mine")

        leftover = []
        image_id_or_name = None
        search = self._search_terms(parts, out_leftover=leftover)
        if len(leftover) > 0:
            image_id_or_name = leftover[0]

        image = self._resolve_one_model(image_id_or_name, Image, search)

        if image is None:
            raise errors.TalusApiError("could not find talus image with id {!r}".format(
                image_id_or_name
            ))

        status = image.status["name"]
        del image.status["name"]
        print("""
           ID: {id}
         Name: {name}
       Status: {status}
         Tags: {tags}
   Base Image: {base_image}""".format(
            id = image.id,
            name = image.name,
            status = "{}{}".format(
                status,
                " ({})".format(image.status) if len(image.status) > 0 else ""
            ),
            tags = ", ".join(image.tags),
            base_image = self._nice_name(image, "base_image") if image.base_image is not None else None,
        ))

        self._show_image_in_tree(image)

    def _show_image_in_tree(self, image):
        curr_image = image
        tree = []
        curr_image = image
        while curr_image.base_image is not None:
            curr_image = Image.find_one(id=curr_image.base_image)
            tree.append(curr_image)
        tree.reverse()

        lines = []
        idx = 0
        for ancestor_image in tree:
            self._get_tree_lines(ancestor_image, lines, indent_level=idx, recurse=False)
            idx += 1

        image.name = Colors.OKGREEN + image.name + Colors.ENDC
        self._get_tree_lines(image, lines, indent_level=len(tree))

        image.refresh()

        print("Snapshot Tree:")
        print("")
        self._print_snapshot_tree([image], lines=lines)

    def _print_snapshot_tree(self, base_images, lines=None):
        if lines is None:
            lines = []
            for base_image in base_images:
                self._get_tree_lines(base_image, lines)

        max_line_length = len(max(lines, key=lambda x: len(self._plain_text((x["text"]))))["text"])

        header_line = ("{:^" + str(max_line_length) + "}    {:20} {:30} {}").format("image name", "status", "id", "tags")
        print(header_line)
        print("-" * len(header_line))

        for line in lines:
            image = line["image"]
            status = image.status.setdefault("name", "")
            if "vnc" in image.status:
                status = image.status["vnc"]["vnc"]["uri"]

            text_line = line["text"]
            text_line += (" " * (max_line_length - len(self._plain_text(line["text"]))))
            print((u"{}    {:20} {:30} {}").format(
                text_line,
                status,
                line["image"].id,
                ",".join(line["image"].tags)
            ))

    def _get_tree_lines(self, image, lines, indent_level=0, recurse=True):
        if indent_level == 0:
            indent = ""
        else:
            indent = Colors.OKBLUE + u"  └──" + Colors.ENDC
            if indent_level > 1:
                indent = ("     " * (indent_level-1)) + indent
            indent += " "

        lines.append({
            "image": image,
            "text": indent + image.name,
        })

        if recurse:
            for child in image.children():
                self._get_tree_lines(child, lines, indent_level+1)
    

    def do_import(self, args):
        """Import an image into Talus

        import FILE -n NAME -o OSID [-d DESC] [-t TAG1,TAG2,..] [-u USER] [-p PASS] [-i]

                    FILE    The file to import
                 -o,--os    ID or name of the operating system model
               -n,--name    The name of the resulting image (default: basename(FILE))
               -d,--desc    A description of the image (default: "")
               -t,--tags    Tags associated with the image (default: [])
            -f,--file-id    The id of an already-uploaded file (NOT A NORMAL USE CASE)
           -u,--username    The username to be used in the image (default: user)
           -p,--password    The password to be used in the image (default: password)
        -i,--interactive    To interact with the imported image for setup (default: False)

        Examples:

        To import an image from VMWare at ``~/images/win7pro.vmdk`` named "win 7 pro test"
        and to be given a chance to perform some manual setup/checks:

            image import ~/images/win7pro.vmdk -n "win 7 pro test" -i -o "win7pro" -t windows7,x64,IE8
        """
        parser = argparse.ArgumentParser()
        parser.add_argument("file", type=str)
        parser.add_argument("--os", "-o")
        parser.add_argument("--name", "-n")
        parser.add_argument("--desc", "-d", default="desc")
        parser.add_argument("--file-id", "-f", default=None)
        parser.add_argument("--tags", "-t", default="")
        parser.add_argument("--username", "-u", default="user")
        parser.add_argument("--password", "-p", default="password")
        parser.add_argument("--interactive", "-i", action="store_true", default=False)

        args = parser.parse_args(shlex.split(args))

        args.tags = args.tags.split(",")
        if args.name is None:
            args.name = os.path.basename(args.file)

        image = self._talus_client.image_import(
            image_path    = args.file,
            image_name    = args.name,
            os_id        = args.os,
            desc        = args.desc,
            tags        = args.tags,
            file_id        = args.file_id,
            username    = args.username,
            password    = args.password
        )

        self._wait_for_image(image, args.interactive)
    
    def do_edit(self, args):
        """Edit an existing image. Interactive mode only
        """
        if args.strip() == "":
            raise errors.TalusApiError("you must provide a name/id of an image to edit it")

        parts = shlex.split(args)
        leftover = []
        image_id_or_name = None
        search = self._search_terms(parts, out_leftover=leftover)
        if len(leftover) > 0:
            image_id_or_name = leftover[0]

        image = self._resolve_one_model(image_id_or_name, Image, search)

        if image is None:
            raise errors.TalusApiError("could not find talus image with id {!r}".format(image_id_or_name))

        while True:
            model_cmd = self._make_model_cmd(image)
            cancelled = model_cmd.cmdloop()
            if cancelled:
                break

            error = False
            if image.os is None:
                self.err("You must specify the os")
                error = True

            if image.name is None or image.name == "":
                self.err("You must specify a name for the image")
                error = True

            if image.base_image is None:
                self.err("You must specify the base_image for your new image")
                error = True

            if error:
                continue

            try:
                image.timestamps = {"modified": time.time()}
                image.save()
                self.ok("edited image {}".format(image.id))
                self.ok("note that this DOES NOT start the image for configuring!")
            except errors.TalusApiError as e:
                self.err(e.message)

            return
    
    def do_create(self, args):
        """Create a new image in talus using an existing base image. Anything not explicitly
        specified will be inherited from the base image, except for the name, which is required.

        create -n NAME -b BASEID_NAME [-d DESC] [-t TAG1,TAG2,..] [-u USER] [-p PASS] [-o OSID] [-i]

                 -o,--os    ID or name of the operating system model
               -b,--base    ID or name of the base image
               -n,--name    The name of the resulting image (default: basename(FILE))
               -d,--desc    A description of the image (default: "")
               -t,--tags    Tags associated with the image (default: [])
                 --shell    Forcefully drop into an interactive shell
        -v,--vagrantfile    A vagrant file that will be used to congfigure the image
        -i,--interactive    To interact with the imported image for setup (default: False)

        Examples:

        To create a new image based on the image with id 222222222222222222222222 and adding
        a new description and allowing for manual user setup:

            image create -b 222222222222222222222222 -d "some new description" -i
        """
        args = shlex.split(args)
        if self._go_interactive(args):
            image = Image()
            self._prep_model(image)
            image.username = "user"
            image.password = "password"
            image.md5 = " "
            image.desc = "some description"
            image.status = {
                "name": "create",
                "vagrantfile": None,
                "user_interaction": True
            }

            while True:
                model_cmd = self._make_model_cmd(image)
                model_cmd.add_field(
                    "interactive",
                    Field(True),
                    lambda x,v: x.status.update({"user_interaction": v}),
                    lambda x: x.status["user_interaction"],
                    desc="If the image requires user interaction for configuration",
                )
                model_cmd.add_field(
                    "vagrantfile",
                    Field(str),
                    lambda x,v: x.status.update({"vagrantfile": open(v).read()}),
                    lambda x: x.status["vagrantfile"],
                    desc="The path to the vagrantfile that will configure the image"
                )
                cancelled = model_cmd.cmdloop()
                if cancelled:
                    break

                error = False
                if image.os is None:
                    self.err("You must specify the os")
                    error = True

                if image.name is None or image.name == "":
                    self.err("You must specify a name for the image")
                    error = True

                if image.base_image is None:
                    self.err("You must specify the base_image for your new image")
                    error = True

                if error:
                    continue

                try:
                    image.timestamps = {"created": time.time()}
                    if self._talus_user not in image.tags:
                        image.tags.append(self._talus_user)
                    image.save()
                    self.ok("created new image {}".format(image.id))
                except errors.TalusApiError as e:
                    self.err(e.message)
                else:
                    self._wait_for_image(image, image.status["user_interaction"])

                return

        parser = self._argparser()
        parser.add_argument("--os", "-o", default=None)
        parser.add_argument("--base", "-b", default=None)
        parser.add_argument("--name", "-n", default=None)
        parser.add_argument("--desc", "-d", default="")
        parser.add_argument("--tags", "-t", default="")
        parser.add_argument("--vagrantfile", "-v", default=None, type=argparse.FileType("rb"))
        parser.add_argument("--interactive", "-i", action="store_true", default=False)

        args = parser.parse_args(args)

        if args.name is None:
            raise errors.TalusApiError("You must specify an image name")

        vagrantfile_contents = None
        if args.vagrantfile is not None:
            vagrantfile_contents = args.vagrantfile.read()

        if args.tags is not None:
            args.tags = args.tags.split(",")

        error = False
        validation = {
            "os"    : "You must set the os",
            "base"    : "You must set the base",
            "name"    : "You must set the name",
        }
        error = False
        for k,v in validation.iteritems():
            if getattr(args, k) is None:
                self.err(v)
                error = True

        if error:
            parser.print_help()
            return

        image = self._talus_client.image_create(
            image_name            = args.name,
            base_image_id_or_name = args.base,
            os_id                 = args.os,
            desc                  = args.desc,
            tags                  = args.tags,
            vagrantfile           = vagrantfile_contents,
            user_interaction      = args.interactive
        )

        self._wait_for_image(image, args.interactive)
    
    def do_configure(self, args):
        """Configure an existing image in talus

        configure ID_OR_NAME [-v PATH_TO_VAGRANTFILE] [-i]

              id_or_name    The ID or name of the image that is to be configured (required)
        -i,--interactive    To interact with the imported image for setup (default: False)
        -v,--vagrantfile    The path to the vagrantfile that should be used to configure the image (default=None)

        Examples:

        To configure an image named "Windows 7 x64 Test", using a vagrantfile found
        at `~/vagrantfiles/UpdateIE` with no interaction:

            configure "Windows 7 x64 Test" --vagrantfile ~/vagrantfiles/UpdateIE
        """
        parser = self._argparser()
        parser.add_argument("image_id_or_name", type=str)
        parser.add_argument("--interactive", "-i", action="store_true", default=False)
        parser.add_argument("--vagrantfile", "-v", default=None, type=argparse.FileType("rb"))

        args = parser.parse_args(shlex.split(args))

        vagrantfile_contents = None
        if args.vagrantfile is not None:
            vagrantfile_contents = args.vagrantfile.read()

        image = self._resolve_one_model(args.image_id_or_name, Image, {})
        children_snapshots = image.children()
        if len(children_snapshots) > 0:
            error_message = "the image {} has dependent snapshots! cannot configure the image".format(
                args.image_id_or_name
            )
            self.err(error_message)
            self._show_image_in_tree(image)
            raise errors.TalusApiError(error_message)

        image = self._talus_client.image_configure(
            args.image_id_or_name,
            vagrantfile=vagrantfile_contents,
            user_interaction=args.interactive
        )

        if image is None:
            return

        self._wait_for_image(image, args.interactive)
    
    def do_delete(self, args):
        """Attempt to delete the specified image. This may fail if the image is the
        base image for another image.

        delete id_or_name

        id_or_name    The ID or name of the image that is to be deleted
        """
        args = shlex.split(args)
        image = self._resolve_one_model(args[0], Image, {})
        if len(image.children()) > 0:
            error_message = "Cannot delete image {}! it has dependent snapshots!".format(
                args[0]
            )
            self.err(error_message)
            self._show_image_in_tree(image)
            raise errors.TalusApiError(error_message)

        image = self._talus_client.image_delete(args[0])

        if image is None:
            return

        try:
            while image.status["name"] == "delete":
                time.sleep(1)

                # will error if the model doesn't exist anymore (which is
                # expected)
                image.refresh()

            if "error" in image.status:
                self.err("could not delete image due to: " + image.status["error"])
            else:
                self.ok("image succesfully deleted")
        except Exception as e:
            # the model will no longer exist in the database, so image.refresh above
            # will raise an exception
            if "model no longer exists" in  str(e):
                self.ok("image successfully deleted!")
            else:
                self.err("could not delete image")

    # ----------------------------
    # UTILITY
    # ----------------------------

    def _wait_for_image(self, image, interactive):
        """Wait for the image to be ready, either for interactive interaction
        or to enter the ready state"""
        if interactive:
            while image.status["name"] != "configuring":
                time.sleep(1)
                image.refresh()

            self.ok("Image is up and running at {}".format(image.status["vnc"]["vnc"]["uri"]))
            self.ok("Shutdown (yes, nicely shut it down) to save your changes")
        else:
            while image.status["name"] != "ready":
                time.sleep(1)
                image.refresh()

            self.ok("image {!r} is ready for use".format(image.name))

#!/usr/bin/env python
# encoding: utf-8

import argparse
import cmd
import os
import shlex
import sys
from tabulate import tabulate
import time

from talus_client.cmds import TalusCmdBase
import talus_client.api
import talus_client.errors as errors
from talus_client.models import Image,Field

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

        fields = []
        for image in self._talus_client.image_iter(**search):
            status = image.status["name"]
            if "vnc" in image.status:
                status +=" ({})".format(image.status["vnc"]["vnc"]["uri"])

            fields.append([
                image.id,
                image.name,
                status,
                image.tags,
                self._nice_name(image, "base_image") if image.base_image is not None else None,
                self._nice_name(image, "os"),
                image.md5,
            ])

        print(tabulate(fields, headers=Image.headers()))
    
    def do_info(self, args):
        """List detailed information about an image

        info ID_OR_NAME

        Examples:

        List info about the image named "Win 7 Pro"

            image info "Win 7 Pro"
        """
        pass

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
            image_name                = args.name,
            base_image_id_or_name    = args.base,
            os_id                    = args.os,
            desc                    = args.desc,
            tags                    = args.tags,
            vagrantfile                = vagrantfile_contents,
            user_interaction        = args.interactive
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
        image = self._talus_client.image_delete(args[0])

        if image is None:
            return

        try:
            while image.status["name"] == "delete":
                time.sleep(1)
                image.refresh()

            if "error" in image.status:
                self.err("could not delete image due to: " + image.status["error"])
            else:
                self.ok("image succesfully deleted")
        except:
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

#!/usr/bin/env python
# encoding: utf-8

import cmd
import json
import os
import pipes
import shlex
import sys
from tabulate import tabulate

import talus_client.utils as utils
from talus_client.utils import Colors
from talus_client.models import *
from talus_client.cmds import TalusCmdBase

def nice_string(val):
    if isinstance(val, dict):
        val = json.dumps(val)
    else:
        val = str(val)

    if len(val) > 60:
        val = val[0:60] + "..."

    for c in val:
        if not (32 <= ord(c) <= 126):
            val = repr(val)
            break
    
    return val

class ParameterCmd(TalusCmdBase):
    def __init__(self, params, code_model, talus_host=None, client=None):
        super(ParameterCmd, self).__init__(talus_host=talus_host, client=client)

        self._params = params
        self._code_model = code_model
        self._param_infos = {}

        for pinfo in self._code_model.params:
            self._param_infos[pinfo["name"]] = pinfo

        to_delete = []
        for k,v in self._params.iteritems():
            if k not in self._param_infos:
                self.warn("previously set parameter {} does not exist anymore".format(k))
                to_delete.append(k)

        for k in to_delete:
            del self._params[k]
    
    def _cast_param_str(self, name, param):
        finfo = self._param_infos[name]
    
    def complete_set(self, text, line, begidx, endidx):
        return filter(lambda x: x.startswith(text), self._param_infos.keys())
    
    def do_set(self, args):
        """Set a parameter
        """
        args = shlex.split(args)

        if len(args) == 0:
            print("wat")
            return

        field_name = args[0]
        if field_name not in self._param_infos:
            self.err("Field named '{}' does not exist, try again".format(field_name))
            return

        finfo = self._param_infos[field_name]

        if finfo["type"]["type"] == "fileset":
            self._handle_set_fileset(args, field_name, finfo)

        elif finfo["type"]["type"] == "component":
            self._handle_set_component(args, field_name, finfo)

        else:
            if len(args) == 1:
                self.err("You must provide a value to set '{}'".format(field_name))
                return

            # just set it
            self._params[field_name] = self._convert_val(finfo, args[1:])

    def _to_bool(self, val):
        if type(val) is bool:
            return val
        if type(val) is str:
            return val.lower() == "true"
        return not (not val)

    def _convert_val(self, param_type, vals):
        switch = {
            "list"    : lambda x: list(x),
            "tuple"   : lambda x: tuple(x),
            # TODO
            # "dict"  : lambda x: dict(x),

            "int"     : lambda x: int(x[0]),
            "float"   : lambda x: float(x[0]),
            "str"     : lambda x: str(x[0]),
            "unicode" : lambda x: unicode(x[0]),
            "bool"    : self._to_bool,
        }
        return switch[param_type["type"]["name"]](vals)
    
    def _handle_set_component(self, args, field_name, finfo):
        base_cls_name = finfo["type"]["name"]

        models = [self._talus_client.code_find(base_cls_name, type="component")]
        descendants = list(self._talus_client.code_iter(type_="component", bases=base_cls_name))
        models += descendants

        pick_component_cls = True
        component = None
        sub_params = {}

        # don't clobber existing settings! be nice and ask the user
        # psychomatic complexity TO THE MAX!! :^(
        if field_name in self._params and self._params[field_name] is not None:
            if len(descendants) == 0:
                pick_component_cls = False
                sub_params = self._params[field_name]["params"]

            else:
                res = self.ask("Do you want to change the component class? (y/n) ")
                if res.strip().lower() in ["y", "yes"]:
                    pick_component_cls = True
                else:
                    pick_component_cls = False
                    component = filter(lambda x: x.name == self._params[field_name]["class"], models)[0]
                    try:
                        sub_params = self._params[field_name]["params"]
                    except KeyError as e:
                        self.err("something wrong happened, can't keep old params, need to reset all values")
                        sub_params = {}

        if len(descendants) > 0 and pick_component_cls:
            component = utils.model_prompt(models, "Which component subclass should be used?")
        elif component is None:
            component = models[0]

        processor = ParameterCmd(sub_params, component, self._talus_host, self._talus_client)
        processor.prompt = self.prompt[:-2] + ":{}> ".format(field_name)
        processor.cmdloop()

        self._params[field_name] = {"class": component.name, "params": sub_params}
    
    def _handle_set_fileset(self, args, field_name, finfo):
        files = list(self._talus_client.fileset_iter())
        if len(args) == 1 or args[1] != "--all":
            files = filter(lambda x: x.job is None, files)

        fields = []
        for x in xrange(len(files)):
            f = files[x]
            mod = f.timestamps
            fields.append([x, f.name, f.id, str(len(f.files))])

        headers = ["idx", "fileset name", "id", "# files"]
        idx = utils.idx_prompt(fields, "Which fileset should be used?", headers=headers)
        if idx is None:
            return

        fset = files[idx]
        self._params[field_name] = fset.id
    
    def do_show(self, args):
        """Show the current fields and their values
        """
        fields = []
        for pinfo in self._code_model.params:
            field_val = self._params.setdefault(pinfo["name"], None)

            # components
            if isinstance(field_val, dict) and "params" in field_val:
                field_val = "({}) {}".format(field_val["class"], field_val["params"])
                
            field_val = nice_string(field_val)
            fields.append([
                Colors.OKBLUE + pinfo["name"] + Colors.ENDC,
                pinfo["type"]["name"],
                Colors.OKGREEN + field_val + Colors.ENDC,
                pinfo["desc"]
            ])

        headers = ["name", "type", "value", "description"]
        headers = [Colors.BRIGHT + Colors.BLACK + x + Colors.ENDC for x in headers]
        print(tabulate(fields, headers=headers))
    
    def do_done(self, args):
        """Be done setting parameters (aka done/quit/save/exit/up)
        """
        if self._validate_fields():
            return True
    
    def _validate_fields(self):
        has_unset = self._print_unset_fields()
        if has_unset:
            res = self.ask("Are you fine with these fields being unset? (y/n) ")
            if res.strip().lower() in ["y", "yes"]:
                return True
            else:
                self.ok("ok, not quitting so you can change those fields you forgot about")
                return False
        else:
            return True

    def _print_unset_fields(self, params=None, path=""):
        if params is None:
            params = self._params

        unset_fields = False
        for k,v in params.iteritems():
            if isinstance(v, dict):
                # is a component
                # TODO this might bite us later if we allow dicts as parameter values...
                has_unset = self._print_unset_fields(v["params"], k + ".")
                if has_unset:
                    unset_fields = True
            else:
                if v is None:
                    unset_fields = True
                    self.warn("{}{} is unset".format(path, k))

        return unset_fields
    
    do_quit = do_done
    do_save = do_done
    do_exit = do_done
    do_up = do_done

class ModelCmd(TalusCmdBase):
    def __init__(self, model, talus_host=None, client=None):
        super(ModelCmd, self).__init__(talus_host=talus_host, client=client)

        self._model = model
        self._update_code_and_param_cmd()
        
        self._shim_fields = {}
    
    def _update_code_and_param_cmd(self):
        if isinstance(self._model, (Job, Task)):
            if isinstance(self._model, Job):
                code = self._talus_client.code_find(self._talus_client.task_find(self._model.task).tool)
            elif isinstance(self._model, Task):
                code = self._talus_client.code_find(self._model.tool)
            self._param_cmd = ParameterCmd(self._model.params, code, self._talus_host, self._talus_client)
        else:
            self._param_cmd = None
    
    def add_field(self, field_name, field_val, setter, getter, desc=""):
        self._shim_fields[field_name] = {
            "type": field_val,
            "setter": setter,
            "getter": getter,
            "desc": desc
        }
    
    def do_show(self, args):
        """Show the current fields and their values
        """
        fields = []

        for field_name in self._model.fields.keys():
            field_val = getattr(self._model, field_name)
            field_val = nice_string(field_val)

            fields.append([
                Colors.OKBLUE + field_name + Colors.ENDC,
                Colors.OKGREEN + field_val + Colors.ENDC,
                self._model.fields[field_name].desc
            ])

        for field_name, field_info in self._shim_fields.iteritems():
            field_val = field_info["getter"](self._model)
            field_desc = field_info["desc"]
            field_val = nice_string(field_val)

            fields.append([
                Colors.OKBLUE + field_name + Colors.ENDC,
                Colors.OKGREEN + field_val + Colors.ENDC,
                field_desc,
            ])
        headers=["name", "value", "description"]
        headers = [Colors.BRIGHT + Colors.BLACK + x + Colors.ENDC for x in headers]
        print(tabulate(fields, headers=headers))
    
    def complete_set(self, text, line, begidx, endidx):
        fields = filter(lambda x: x.startswith(text), self._model.fields.keys())
        fields += filter(lambda x: x.startswith(text), self._shim_fields.keys())
        return fields
    
    def do_set(self, args):
        """Set parameter
        """

        orig_args = args
        args = shlex.split(args)

        if len(args) == 0:
            print("wat - you must supply a fieldname to set")
            return

        field_name = args[0]

        if field_name == "params":
            self._param_cmd.prompt = self.prompt[:-2] + ":params> "
            self._param_cmd.cmdloop("\nEditing params\n")
            return

        if field_name not in self._model.fields and field_name not in self._shim_fields:
            self.err("Field '{}' does not exist. Try again".format(field_name))
            return

        is_shim = False
        if field_name in self._model.fields:
            field_cls = self._model.fields[field_name]
        else:
            is_shim = True
            field_cls = self._shim_fields[field_name]["type"]

        if isinstance(field_cls, RefField):
            refd_cls = field_cls.get_ref_cls()
            search = field_cls.search
            if search is None:
                search = {}

            while True:
                refd_model = utils.model_prompt(
                    refd_cls.objects(api_base=None, **search),
                    "Which one should {!r} be set to?".format(field_name),
                    new_allowed=(refd_cls.interactive_create_command is not None)
                )
                if refd_model is None:
                    return
                elif refd_model == "NEW":
                    self._root.onecmd(refd_cls.interactive_create_command)
                    continue
                else:
                    break

            if not is_shim:
                setattr(self._model, field_name, refd_model.id)
            else:
                try:
                    self._shim_fields[field_name]["setter"](self._model, refd_model.id)
                except Exception as e:
                    self.err(e.message)
                    return

            # update the code ref
            if (isinstance(self._model, Job) and field_name == "task"):
                self._model.params = refd_model.params
                self._update_code_and_param_cmd()
            elif (isinstance(self._model, Task) and field_name == "tool"):
                self._update_code_and_param_cmd()
        else:
            if len(args) < 2:
                print("Error, you must supply a value to set {!r}".format(field_name))
                return

            if isinstance(field_cls.value, (list,tuple)):
                field_value = args[1:]
            else:
                field_value = args[1]

            field_value = field_cls.cast(field_value)
            if not field_cls.validate(field_value):
                self.err("Invalid value")
                return

            if not is_shim:
                setattr(self._model, field_name, field_value)
            else:
                try:
                    self._shim_fields[field_name]["setter"](self._model, field_value)
                except Exception as e:
                    self.err(e.message)
                    return
    
    def do_done(self, args):
        """Be done setting parameters (aka done/quit/save/exit/up)
        """
        return True
    
    do_quit = do_done
    do_save = do_done
    do_exit = do_done
    do_up = do_done
    

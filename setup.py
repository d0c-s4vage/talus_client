#!/usr/bin/env python
# encoding: utf-8

import os, sys
from setuptools import setup

setup(
    # metadata
    name='talus_client',
    description='A command-line client for talus',
    long_description="""
		talus is a command-line client for Talus.
    """,
    license='MIT',
    version='0.1',
    author='Optiv Labs',
    maintainer='Optiv Labs',
    author_email='james.johnson@optiv.com',
    url='https://github.com/optiv-labs/talus/tree/master/src/client',
    platforms='Cross Platform',
	install_requires = open(os.path.join(os.path.dirname(__file__), "requirements.txt")).read().split("\n"),
    classifiers = [
        'Programming Language :: Python :: 2',
		# hos not been tested on Python 3
        # 'Programming Language :: Python :: 3',
	],
    scripts = [
            os.path.join("bin", "talus"),
    ],
    package_data = {
        "talus_client": ["adjectives.txt", "nouns.txt"],
    },
    include_package_data=True,
    packages=['talus_client', 'talus_client.cmds'],
)

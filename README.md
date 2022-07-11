ap-swarm-launcher
=================

Simplified ArduPilot SITL launcher for multi-drone simulations. Runs multiple
ArduPilot SITL instances in parallel, managed by a central process supervisor
that merges the standard output streams of individual processes and configures
the SITL instances to start the drones from a grid-like formation.

Installation
------------

We use `poetry` to manage a Python virtual environment that will contain all
the dependencies. Install `poetry` first if you don't have it yet, then follow
these steps:

1. Clone this repository.

2. Run `poetry install` to create a virtualenv and install all the dependencies
   in it.

3. Run `poetry run ap-sitl-swarm SITL_PATH` to launch a single SITL instance
   with a pre-compiled SITL executable at `${SITL_PATH}`. Use the `-h` switch
   for more options.

Tested on Linux and macOS. May or may not work on Windows.

License
-------

Copyright 2022 CollMot Robotics Ltd.

Permission is hereby granted, free of charge, to any person obtaining a copy of
this software and associated documentation files (the "Software"), to deal in
the Software without restriction, including without limitation the rights to
use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
of the Software, and to permit persons to whom the Software is furnished to do
so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

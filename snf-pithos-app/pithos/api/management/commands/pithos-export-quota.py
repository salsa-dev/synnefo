# Copyright 2012 GRNET S.A. All rights reserved.
#
# Redistribution and use in source and binary forms, with or
# without modification, are permitted provided that the following
# conditions are met:
#
#   1. Redistributions of source code must retain the above
#      copyright notice, this list of conditions and the following
#      disclaimer.
#
#   2. Redistributions in binary form must reproduce the above
#      copyright notice, this list of conditions and the following
#      disclaimer in the documentation and/or other materials
#      provided with the distribution.
#
# THIS SOFTWARE IS PROVIDED BY GRNET S.A. ``AS IS'' AND ANY EXPRESS
# OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
# WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR
# PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL GRNET S.A OR
# CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
# SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
# LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF
# USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED
# AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
# LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
# ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
# POSSIBILITY OF SUCH DAMAGE.
#
# The views and conclusions contained in the software and
# documentation are those of the authors and should not be
# interpreted as representing official policies, either expressed
# or implied, of GRNET S.A.

from optparse import make_option

from django.core.management.base import NoArgsCommand, CommandError

from sqlalchemy.sql import select, and_

from pithos.api.util import get_backend

import os

backend = get_backend()
table = {}
table['nodes'] = backend.node.nodes
table['policy'] = backend.node.policy
conn = backend.node.conn


class Command(NoArgsCommand):
    help = "Export account quota policies"

    option_list = NoArgsCommand.option_list + (
        make_option('--location',
                    dest='location',
                    default='exported_policies',
                    help="Where to save the output file"),
    )

    def handle_noargs(self, **options):
        # retrieve account policies
        s = select([table['nodes'].c.path, table['policy'].c.value])
        s = s.where(and_(table['nodes'].c.node != 0,
                         table['nodes'].c.parent == 0))
        s = s.where(table['nodes'].c.node == table['policy'].c.node)
        s = s.where(table['policy'].c.key == 'quota')

        location = os.path.abspath(options['location'])
        try:
            f = open(location, 'w')
        except IOError, e:
            raise CommandError(e)

        for p in conn.execute(s).fetchall():
            f.write(' '.join(
                [p.path, 'pithos+.diskspace', p.value, '0', '0', '0']))
            f.write('\n')
        f.close()
        backend.close()

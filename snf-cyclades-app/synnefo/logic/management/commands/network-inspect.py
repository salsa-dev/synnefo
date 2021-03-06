# Copyright (C) 2010-2014 GRNET S.A.
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

from optparse import make_option

from django.core.management.base import CommandError

from snf_django.management.commands import SynnefoCommand
from synnefo.management import pprint, common


class Command(SynnefoCommand):
    help = "Inspect a network on DB and Ganeti."
    args = "<network_id>"

    option_list = SynnefoCommand.option_list + (
        make_option(
            '--displayname',
            action='store_true',
            dest='displayname',
            default=False,
            help="Display both uuid and display name"),
    )

    def handle(self, *args, **options):
        if len(args) != 1:
            raise CommandError("Please provide a network ID.")

        network = common.get_resource("network", args[0])
        displayname = options['displayname']

        pprint.pprint_network(network, display_mails=displayname,
                              stdout=self.stdout)
        self.stdout.write("\n\n")
        pprint.pprint_network_subnets(network, stdout=self.stdout)
        self.stdout.write("\n\n")
        pprint.pprint_network_backends(network, stdout=self.stdout)
        self.stdout.write("\n\n")
        pprint.pprint_network_in_ganeti(network, stdout=self.stdout)

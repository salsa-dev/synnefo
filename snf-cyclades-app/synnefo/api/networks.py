# Copyright 2011-2012 GRNET S.A. All rights reserved.
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

from logging import getLogger

from django.conf.urls.defaults import patterns
from django.conf import settings
from django.db.models import Q
from django.db import transaction
from django.http import HttpResponse
from django.template.loader import render_to_string
from django.utils import simplejson as json

from synnefo.api import util
from synnefo.api.actions import network_actions
from synnefo.api.common import method_not_allowed
from synnefo.api.faults import (ServiceUnavailable, BadRequest, Forbidden,
                                NetworkInUse, OverLimit)
from synnefo import quotas
from synnefo.db.models import Network
from synnefo.db.pools import EmptyPool
from synnefo.logic import backend


log = getLogger('synnefo.api')

urlpatterns = patterns('synnefo.api.networks',
    (r'^(?:/|.json|.xml)?$', 'demux'),
    (r'^/detail(?:.json|.xml)?$', 'list_networks', {'detail': True}),
    (r'^/(\w+)(?:.json|.xml)?$', 'network_demux'),
    (r'^/(\w+)/action(?:.json|.xml)?$', 'network_action'),
)


def demux(request):
    if request.method == 'GET':
        return list_networks(request)
    elif request.method == 'POST':
        return create_network(request)
    else:
        return method_not_allowed(request)


def network_demux(request, network_id):
    if request.method == 'GET':
        return get_network_details(request, network_id)
    elif request.method == 'PUT':
        return update_network_name(request, network_id)
    elif request.method == 'DELETE':
        return delete_network(request, network_id)
    else:
        return method_not_allowed(request)


def network_to_dict(network, user_id, detail=True):
    d = {'id': str(network.id), 'name': network.name}
    if detail:
        d['cidr'] = network.subnet
        d['cidr6'] = network.subnet6
        d['gateway'] = network.gateway
        d['gateway6'] = network.gateway6
        d['dhcp'] = network.dhcp
        d['type'] = network.flavor
        d['updated'] = util.isoformat(network.updated)
        d['created'] = util.isoformat(network.created)
        d['status'] = network.state
        d['public'] = network.public

        attachments = [util.construct_nic_id(nic)
                       for nic in network.nics.filter(machine__userid=user_id)\
                                              .order_by('machine')]
        d['attachments'] = {'values': attachments}
    return d


def render_network(request, networkdict, status=200):
    if request.serialization == 'xml':
        data = render_to_string('network.xml', {'network': networkdict})
    else:
        data = json.dumps({'network': networkdict})
    return HttpResponse(data, status=status)


@util.api_method('GET')
def list_networks(request, detail=False):
    # Normal Response Codes: 200, 203
    # Error Response Codes: computeFault (400, 500),
    #                       serviceUnavailable (503),
    #                       unauthorized (401),
    #                       badRequest (400),
    #                       overLimit (413)

    log.debug('list_networks detail=%s', detail)
    since = util.isoparse(request.GET.get('changes-since'))
    user_networks = Network.objects.filter(Q(userid=request.user_uniq) |
                                           Q(public=True))

    if since:
        user_networks = user_networks.filter(updated__gte=since)
        if not user_networks:
            return HttpResponse(status=304)
    else:
        user_networks = user_networks.filter(deleted=False)

    networks = [network_to_dict(network, request.user_uniq, detail)
                for network in user_networks.order_by('id')]

    if request.serialization == 'xml':
        data = render_to_string('list_networks.xml', {
            'networks': networks,
            'detail': detail})
    else:
        data = json.dumps({'networks': {'values': networks}})

    return HttpResponse(data, status=200)


@util.api_method('POST')
@quotas.uses_commission
@transaction.commit_manually
def create_network(serials, request):
    # Normal Response Code: 202
    # Error Response Codes: computeFault (400, 500),
    #                       serviceUnavailable (503),
    #                       unauthorized (401),
    #                       badMediaType(415),
    #                       badRequest (400),
    #                       forbidden (403)
    #                       overLimit (413)

    try:
        req = util.get_request_dict(request)
        log.info('create_network %s', req)

        try:
            d = req['network']
            name = d['name']
            # TODO: Fix this temp values:
            subnet = d.get('cidr', '192.168.1.0/24')
            subnet6 = d.get('cidr6', None)
            gateway = d.get('gateway', None)
            gateway6 = d.get('gateway6', None)
            flavor = d.get('type', 'MAC_FILTERED')
            public = d.get('public', False)
            dhcp = d.get('dhcp', True)
        except (KeyError, ValueError):
            raise BadRequest('Malformed request.')

        if public:
            raise Forbidden('Can not create a public network.')

        if flavor not in Network.FLAVORS.keys():
            raise BadRequest("Invalid network flavors %s" % flavor)

        if flavor not in settings.API_ENABLED_NETWORK_FLAVORS:
            raise Forbidden("Can not create %s network" % flavor)

        cidr_block = int(subnet.split('/')[1])
        if not util.validate_network_size(cidr_block):
            raise OverLimit("Unsupported network size.")

        user_id = request.user_uniq
        serial = quotas.issue_network_commission(user_id)
        serials.append(serial)
        # Make the commission accepted, since in the end of this
        # transaction the Network will have been created in the DB.
        serial.accepted = True
        serial.save()

        try:
            mode, link, mac_prefix, tags = util.values_from_flavor(flavor)
            network = Network.objects.create(
                    name=name,
                    userid=user_id,
                    subnet=subnet,
                    subnet6=subnet6,
                    gateway=gateway,
                    gateway6=gateway6,
                    dhcp=dhcp,
                    flavor=flavor,
                    mode=mode,
                    link=link,
                    mac_prefix=mac_prefix,
                    tags=tags,
                    action='CREATE',
                    state='PENDING',
                    serial=serial)
        except EmptyPool:
            log.error("Failed to allocate resources for network of type: %s",
                      flavor)
            raise ServiceUnavailable("Failed to allocate network resources")

        # Create BackendNetwork entries for each Backend
        network.create_backend_network()
    except:
        transaction.rollback()
    else:
        transaction.commit()

    # Create the network in the actual backends
    backend.create_network(network)

    networkdict = network_to_dict(network, request.user_uniq)
    response = render_network(request, networkdict, status=202)

    return response


@util.api_method('GET')
def get_network_details(request, network_id):
    # Normal Response Codes: 200, 203
    # Error Response Codes: computeFault (400, 500),
    #                       serviceUnavailable (503),
    #                       unauthorized (401),
    #                       badRequest (400),
    #                       itemNotFound (404),
    #                       overLimit (413)

    log.debug('get_network_details %s', network_id)
    net = util.get_network(network_id, request.user_uniq)
    netdict = network_to_dict(net, request.user_uniq)
    return render_network(request, netdict)


@util.api_method('PUT')
def update_network_name(request, network_id):
    # Normal Response Code: 204
    # Error Response Codes: computeFault (400, 500),
    #                       serviceUnavailable (503),
    #                       unauthorized (401),
    #                       badRequest (400),
    #                       forbidden (403)
    #                       badMediaType(415),
    #                       itemNotFound (404),
    #                       overLimit (413)

    req = util.get_request_dict(request)
    log.info('update_network_name %s', network_id)

    try:
        name = req['network']['name']
    except (TypeError, KeyError):
        raise BadRequest('Malformed request.')

    net = util.get_network(network_id, request.user_uniq)
    if net.public:
        raise Forbidden('Can not rename the public network.')
    if net.deleted:
        raise Network.DeletedError
    net.name = name
    net.save()
    return HttpResponse(status=204)


@util.api_method('DELETE')
@transaction.commit_on_success
def delete_network(request, network_id):
    # Normal Response Code: 204
    # Error Response Codes: computeFault (400, 500),
    #                       serviceUnavailable (503),
    #                       unauthorized (401),
    #                       forbidden (403)
    #                       itemNotFound (404),
    #                       overLimit (413)

    log.info('delete_network %s', network_id)
    net = util.get_network(network_id, request.user_uniq, for_update=True)
    if net.public:
        raise Forbidden('Can not delete the public network.')

    if net.deleted:
        raise Network.DeletedError

    if net.machines.all():  # Nics attached on network
        raise NetworkInUse('Machines are connected to network.')


    net.action = 'DESTROY'
    net.save()

    backend.delete_network(net)
    return HttpResponse(status=204)


@util.api_method('POST')
def network_action(request, network_id):
    req = util.get_request_dict(request)
    log.debug('network_action %s %s', network_id, req)
    if len(req) != 1:
        raise BadRequest('Malformed request.')

    net = util.get_network(network_id, request.user_uniq)
    if net.public:
        raise Forbidden('Can not modify the public network.')
    if net.deleted:
        raise Network.DeletedError

    try:
        key = req.keys()[0]
        val = req[key]
        assert isinstance(val, dict)
        return network_actions[key](request, net, req[key])
    except KeyError:
        raise BadRequest('Unknown action.')
    except AssertionError:
        raise BadRequest('Invalid argument.')

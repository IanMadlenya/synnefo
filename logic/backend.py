# Copyright 2011 GRNET S.A. All rights reserved.
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

from django.conf import settings
from django.db import transaction

from synnefo.db.models import (VirtualMachine, Network, NetworkInterface,
                                NetworkLink)
from synnefo.logic import utils
from synnefo.util.rapi import GanetiRapiClient


rapi = GanetiRapiClient(*settings.GANETI_CLUSTER_INFO)


def process_op_status(vm, jobid, opcode, status, logmsg):
    """Process a job progress notification from the backend

    Process an incoming message from the backend (currently Ganeti).
    Job notifications with a terminating status (sucess, error, or canceled),
    also update the operating state of the VM.

    """
    if (opcode not in [x[0] for x in VirtualMachine.BACKEND_OPCODES] or
       status not in [x[0] for x in VirtualMachine.BACKEND_STATUSES]):
        raise VirtualMachine.InvalidBackendMsgError(opcode, status)

    vm.backendjobid = jobid
    vm.backendjobstatus = status
    vm.backendopcode = opcode
    vm.backendlogmsg = logmsg

    # Notifications of success change the operating state
    if status == 'success' and VirtualMachine.OPER_STATE_FROM_OPCODE[opcode] is not None:
        utils.update_state(vm, VirtualMachine.OPER_STATE_FROM_OPCODE[opcode])
        # Set the deleted flag explicitly, to cater for admin-initiated removals
        if opcode == 'OP_INSTANCE_REMOVE':
            vm.deleted = True

    # Special case: if OP_INSTANCE_CREATE fails --> ERROR
    if status in ('canceled', 'error') and opcode == 'OP_INSTANCE_CREATE':
        utils.update_state(vm, 'ERROR')
    # Any other notification of failure leaves the operating state unchanged

    vm.save()


@transaction.commit_on_success
def process_net_status(vm, nics):
    """Process a net status notification from the backend

    Process an incoming message from the Ganeti backend,
    detailing the NIC configuration of a VM instance.

    Update the state of the VM in the DB accordingly.
    """
    
    vm.nics.all().delete()
    for i, nic in enumerate(nics):
        if i == 0:
            net = Network.objects.get(public=True)
        else:
            try:
                link = NetworkLink.objects.get(name=nic['link'])
            except NetworkLink.DoesNotExist:
                # Cannot find an instance of NetworkLink for
                # the link attribute specified in the notification
                raise NetworkLink.DoesNotExist("Cannot find a NetworkLink "
                    "object for link='%s'" % nic['link'])
            net = link.network
            if net is None:
                raise Network.DoesNotExist("NetworkLink for link='%s' not "
                    "associated with an existing Network instance." %
                    nic['link'])

        vm.nics.create(
            network=net,
            index=i,
            mac=nic.get('mac', ''),
            ipv4=nic.get('ip', ''))
    vm.save()


def start_action(vm, action):
    """Update the state of a VM when a new action is initiated."""
    if not action in [x[0] for x in VirtualMachine.ACTIONS]:
        raise VirtualMachine.InvalidActionError(action)

    # No actions to deleted and no actions beside destroy to suspended VMs
    if vm.deleted:
        raise VirtualMachine.DeletedError
   
    # No actions to machines being built. They may be destroyed, however.
    if vm.operstate == 'BUILD' and action != 'DESTROY':
        raise VirtualMachine.BuildingError
    
    vm.action = action
    vm.backendjobid = None
    vm.backendopcode = None
    vm.backendjobstatus = None
    vm.backendlogmsg = None

    # Update the relevant flags if the VM is being suspended or destroyed
    if action == "DESTROY":
        vm.deleted = True
    elif action == "SUSPEND":
        vm.suspended = True
    elif action == "START":
        vm.suspended = False
    vm.save()


def create_instance(vm, flavor, password):
    # FIXME: `password` must be passed to the Ganeti OS provider via CreateInstance()
    
    nic = {'ip': 'pool', 'mode': 'routed', 'link': settings.GANETI_PUBLIC_LINK}
    
    return rapi.CreateInstance(
        mode='create',
        name=vm.backend_id,
        disk_template='plain',
        disks=[{"size": 2000}],         #FIXME: Always ask for a 2GB disk for now
        nics=[nic],
        os='debootstrap+default',       #TODO: select OS from imageRef
        ip_check=False,
        name_check=False,
        pnode=rapi.GetNodes()[0],       #TODO: verify if this is necessary
        dry_run=settings.TEST,
        beparams=dict(auto_balance=True, vcpus=flavor.cpu, memory=flavor.ram))

def delete_instance(vm):
    start_action(vm, 'DESTROY')
    rapi.DeleteInstance(vm.backend_id, dry_run=settings.TEST)
    vm.nics.all().delete()


def reboot_instance(vm, reboot_type):
    assert reboot_type in ('soft', 'hard')
    rapi.RebootInstance(vm.backend_id, reboot_type, dry_run=settings.TEST)


def startup_instance(vm):
    start_action(vm, 'START')
    rapi.StartupInstance(vm.backend_id, dry_run=settings.TEST)


def shutdown_instance(vm):
    start_action(vm, 'STOP')
    rapi.ShutdownInstance(vm.backend_id, dry_run=settings.TEST)


def get_instance_console(vm):
    return rapi.GetInstanceConsole(vm.backend_id)


def create_network_link():
    try:
        last = NetworkLink.objects.order_by('-index')[0]
        index = last.index + 1
    except IndexError:
        index = 1
    
    if index <= settings.GANETI_MAX_LINK_NUMBER:
        name = '%s%d' % (settings.GANETI_LINK_PREFIX, index)
        return NetworkLink.objects.create(index=index, name=name, available=True)
    return None     # All link slots are filled

@transaction.commit_on_success
def create_network(name, owner):
    try:
        link = NetworkLink.objects.filter(available=True)[0]
    except IndexError:
        link = create_network_link()
        if not link:
            return None
    
    network = Network.objects.create(
        name=name,
        owner=owner,
        state='ACTIVE',
        link=link)
    
    link.network = network
    link.available = False
    link.save()
    
    return network

@transaction.commit_on_success
def delete_network(net):
    link = net.link
    if link.name != settings.GANETI_NULL_LINK:
        link.available = True
        link.network = None
        link.save()
    
    for vm in net.machines.all():
        disconnect_from_network(vm, net)
        vm.save()
    net.state = 'DELETED'
    net.save()

def connect_to_network(vm, net):
    nic = {'mode': 'bridged', 'link': net.link.name}
    rapi.ModifyInstance(vm.backend_id,
        nics=[('add', nic)],
        dry_run=settings.TEST)

def disconnect_from_network(vm, net):
    nics = vm.nics.filter(network__public=False).order_by('index')
    new_nics = [nic for nic in nics if nic.network != net]
    if new_nics == nics:
        return      # Nothing to remove
    ops = [('remove', {})]
    for i, nic in enumerate(new_nics):
        ops.append((i + 1, {
            'mode': 'bridged',
            'link': nic.network.link.name}))
    rapi.ModifyInstance(vm.backend_id, nics=ops, dry_run=settings.TEST)

def set_firewall_profile(vm, profile):
    if profile == 'ENABLED':
        to_delete = settings.GANETI_FIREWALL_DISABLED_TAG
        to_add = settings.GANETI_FIREWALL_ENABLED_TAG
    elif profile == 'DISABLED':
        to_delete = settings.GANETI_FIREWALL_ENABLED_TAG
        to_add = settings.GANETI_FIREWALL_DISABLED_TAG
    else:
        raise ValueError("Unsopported Firewall Profile: %s" % profile)
    
    rapi.DeleteInstanceTags(vm.backend_id, [to_delete], dry_run=settings.TEST)
    rapi.AddInstanceTags(vm.backend_id, [to_add], dry_run=settings.TEST)

# Copyright 2011-2013 GRNET S.A. All rights reserved.
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
from datetime import datetime, timedelta

from synnefo.db.models import (Backend, VirtualMachine, Network,
                               BackendNetwork, BACKEND_STATUSES,
                               pooled_rapi_client, VirtualMachineDiagnostic,
                               Flavor, IPAddress, IPAddressLog)
from synnefo.logic import utils, ips
from synnefo import quotas
from synnefo.api.util import release_resource
from synnefo.util.mac2eui64 import mac2eui64
from synnefo.logic import rapi

from logging import getLogger
log = getLogger(__name__)


_firewall_tags = {
    'ENABLED': settings.GANETI_FIREWALL_ENABLED_TAG,
    'DISABLED': settings.GANETI_FIREWALL_DISABLED_TAG,
    'PROTECTED': settings.GANETI_FIREWALL_PROTECTED_TAG}

_reverse_tags = dict((v.split(':')[3], k) for k, v in _firewall_tags.items())

# Timeout in seconds for building NICs. After this period the NICs considered
# stale and removed from DB.
BUILDING_NIC_TIMEOUT = timedelta(seconds=180)

SIMPLE_NIC_FIELDS = ["state", "mac", "network", "firewall_profile", "index"]
COMPLEX_NIC_FIELDS = ["ipv4_address", "ipv6_address"]
NIC_FIELDS = SIMPLE_NIC_FIELDS + COMPLEX_NIC_FIELDS
UNKNOWN_NIC_PREFIX = "unknown-"


def handle_vm_quotas(vm, job_id, job_opcode, job_status, job_fields):
    """Handle quotas for updated VirtualMachine.

    Update quotas for the updated VirtualMachine based on the job that run on
    the Ganeti backend. If a commission has been already issued for this job,
    then this commission is just accepted or rejected based on the job status.
    Otherwise, a new commission for the given change is issued, that is also in
    force and auto-accept mode. In this case, previous commissions are
    rejected, since they reflect a previous state of the VM.

    """
    if job_status not in rapi.JOB_STATUS_FINALIZED:
        return vm

    # Check successful completion of a job will trigger any quotable change in
    # the VM state.
    action = utils.get_action_from_opcode(job_opcode, job_fields)
    if action == "BUILD":
        # Quotas for new VMs are automatically accepted by the API
        return vm
    commission_info = quotas.get_commission_info(vm, action=action,
                                                 action_fields=job_fields)

    if vm.task_job_id == job_id and vm.serial is not None:
        # Commission for this change has already been issued. So just
        # accept/reject it. Special case is OP_INSTANCE_CREATE, which even
        # if fails, must be accepted, as the user must manually remove the
        # failed server
        serial = vm.serial
        if job_status == rapi.JOB_STATUS_SUCCESS:
            quotas.accept_serial(serial)
        elif job_status in [rapi.JOB_STATUS_ERROR, rapi.JOB_STATUS_CANCELED]:
            log.debug("Job %s failed. Rejecting related serial %s", job_id,
                      serial)
            quotas.reject_serial(serial)
        vm.serial = None
    elif job_status == rapi.JOB_STATUS_SUCCESS and commission_info is not None:
        log.debug("Expected job was %s. Processing job %s. Commission for"
                  " this job: %s", vm.task_job_id, job_id, commission_info)
        # Commission for this change has not been issued, or the issued
        # commission was unaware of the current change. Reject all previous
        # commissions and create a new one in forced mode!
        commission_name = ("client: dispatcher, resource: %s, ganeti_job: %s"
                           % (vm, job_id))
        quotas.handle_resource_commission(vm, action,
                                          commission_info=commission_info,
                                          commission_name=commission_name,
                                          force=True,
                                          auto_accept=True)
        log.debug("Issued new commission: %s", vm.serial)

    return vm


@transaction.commit_on_success
def process_op_status(vm, etime, jobid, opcode, status, logmsg, nics=None,
                      job_fields=None):
    """Process a job progress notification from the backend

    Process an incoming message from the backend (currently Ganeti).
    Job notifications with a terminating status (sucess, error, or canceled),
    also update the operating state of the VM.

    """
    # See #1492, #1031, #1111 why this line has been removed
    #if (opcode not in [x[0] for x in VirtualMachine.BACKEND_OPCODES] or
    if status not in [x[0] for x in BACKEND_STATUSES]:
        raise VirtualMachine.InvalidBackendMsgError(opcode, status)

    vm.backendjobid = jobid
    vm.backendjobstatus = status
    vm.backendopcode = opcode
    vm.backendlogmsg = logmsg

    if status not in rapi.JOB_STATUS_FINALIZED:
        vm.save()
        return

    if job_fields is None:
        job_fields = {}
    state_for_success = VirtualMachine.OPER_STATE_FROM_OPCODE.get(opcode)

    # Notifications of success change the operating state
    if status == rapi.JOB_STATUS_SUCCESS:
        if state_for_success is not None:
            vm.operstate = state_for_success
        beparams = job_fields.get("beparams", None)
        if beparams:
            # Change the flavor of the VM
            _process_resize(vm, beparams)
        # Update backendtime only for jobs that have been successfully
        # completed, since only these jobs update the state of the VM. Else a
        # "race condition" may occur when a successful job (e.g.
        # OP_INSTANCE_REMOVE) completes before an error job and messages arrive
        # in reversed order.
        vm.backendtime = etime

    if status in rapi.JOB_STATUS_FINALIZED and nics is not None:
        # Update the NICs of the VM
        _process_net_status(vm, etime, nics)

    # Special case: if OP_INSTANCE_CREATE fails --> ERROR
    if opcode == 'OP_INSTANCE_CREATE' and status in (rapi.JOB_STATUS_CANCELED,
                                                     rapi.JOB_STATUS_ERROR):
        vm.operstate = 'ERROR'
        vm.backendtime = etime
        # Update state of associated NICs
        vm.nics.all().update(state="ERROR")
    elif opcode == 'OP_INSTANCE_REMOVE':
        # Special case: OP_INSTANCE_REMOVE fails for machines in ERROR,
        # when no instance exists at the Ganeti backend.
        # See ticket #799 for all the details.
        if (status == rapi.JOB_STATUS_SUCCESS or
           (status == rapi.JOB_STATUS_ERROR and not vm_exists_in_backend(vm))):
            # VM has been deleted
            for nic in vm.nics.all():
                # Release the IP
                remove_nic_ips(nic)
                # And delete the NIC.
                nic.delete()
            vm.deleted = True
            vm.operstate = state_for_success
            vm.backendtime = etime
            status = rapi.JOB_STATUS_SUCCESS

    if status in rapi.JOB_STATUS_FINALIZED:
        # Job is finalized: Handle quotas/commissioning
        vm = handle_vm_quotas(vm, job_id=jobid, job_opcode=opcode,
                              job_status=status, job_fields=job_fields)
        # and clear task fields
        if vm.task_job_id == jobid:
            vm.task = None
            vm.task_job_id = None

    vm.save()


def _process_resize(vm, beparams):
    """Change flavor of a VirtualMachine based on new beparams."""
    old_flavor = vm.flavor
    vcpus = beparams.get("vcpus", old_flavor.cpu)
    ram = beparams.get("maxmem", old_flavor.ram)
    if vcpus == old_flavor.cpu and ram == old_flavor.ram:
        return
    try:
        new_flavor = Flavor.objects.get(cpu=vcpus, ram=ram,
                                        disk=old_flavor.disk,
                                        disk_template=old_flavor.disk_template)
    except Flavor.DoesNotExist:
        raise Exception("Cannot find flavor for VM")
    vm.flavor = new_flavor
    vm.save()


@transaction.commit_on_success
def process_net_status(vm, etime, nics):
    """Wrap _process_net_status inside transaction."""
    _process_net_status(vm, etime, nics)


def _process_net_status(vm, etime, nics):
    """Process a net status notification from the backend

    Process an incoming message from the Ganeti backend,
    detailing the NIC configuration of a VM instance.

    Update the state of the VM in the DB accordingly.

    """
    ganeti_nics = process_ganeti_nics(nics)
    db_nics = dict([(nic.id, nic)
                    for nic in vm.nics.prefetch_related("ips__subnet")])

    # Get X-Lock on backend before getting X-Lock on network IP pools, to
    # guarantee that no deadlock will occur with Backend allocator.
    Backend.objects.select_for_update().get(id=vm.backend_id)

    for nic_name in set(db_nics.keys()) | set(ganeti_nics.keys()):
        db_nic = db_nics.get(nic_name)
        ganeti_nic = ganeti_nics.get(nic_name)
        if ganeti_nic is None:
            # NIC exists in DB but not in Ganeti. If the NIC is in 'building'
            # state for more than 5 minutes, then we remove the NIC.
            # TODO: This is dangerous as the job may be stack in the queue, and
            # releasing the IP may lead to duplicate IP use.
            if db_nic.state != "BUILD" or\
                (db_nic.state == "BUILD" and
                 etime > db_nic.created + BUILDING_NIC_TIMEOUT):
                remove_nic_ips(db_nic)
                db_nic.delete()
            else:
                log.warning("Ignoring recent building NIC: %s", db_nic)
        elif db_nic is None:
            msg = ("NIC/%s of VM %s does not exist in DB! Cannot automatically"
                   " fix this issue!" % (nic_name, vm))
            log.error(msg)
            continue
        elif not nics_are_equal(db_nic, ganeti_nic):
            for f in SIMPLE_NIC_FIELDS:
                # Update the NIC in DB with the values from Ganeti NIC
                setattr(db_nic, f, ganeti_nic[f])
                db_nic.save()

            # Special case where the IPv4 address has changed, because you
            # need to release the old IPv4 address and reserve the new one
            ipv4_address = ganeti_nic["ipv4_address"]
            if db_nic.ipv4_address != ipv4_address:
                change_address_of_port(db_nic, vm.userid,
                                       old_address=db_nic.ipv4_address,
                                       new_address=ipv4_address,
                                       version=4)

            ipv6_address = ganeti_nic["ipv6_address"]
            if db_nic.ipv6_address != ipv6_address:
                change_address_of_port(db_nic, vm.userid,
                                       old_address=db_nic.ipv6_address,
                                       new_address=ipv6_address,
                                       version=6)

    vm.backendtime = etime
    vm.save()


def change_address_of_port(port, userid, old_address, new_address, version):
    """Change."""
    if old_address is not None:
        msg = ("IPv%s Address of server '%s' changed from '%s' to '%s'"
               % (version, port.machine_id, old_address, new_address))
        log.critical(msg)

    # Remove the old IP address
    remove_nic_ips(port, version=version)

    if version == 4:
        ipaddress = ips.allocate_ip(port.network, userid, address=new_address)
        ipaddress.nic = port
        ipaddress.save()
    elif version == 6:
        subnet6 = port.network.subnet6
        ipaddress = IPAddress.objects.create(userid=userid,
                                             network=port.network,
                                             subnet=subnet6,
                                             nic=port,
                                             address=new_address)
    else:
        raise ValueError("Unknown version: %s" % version)

    # New address log
    ip_log = IPAddressLog.objects.create(server_id=port.machine_id,
                                         network_id=port.network_id,
                                         address=new_address,
                                         active=True)
    log.info("Created IP log entry '%s' for address '%s' to server '%s'",
             ip_log.id, new_address, port.machine_id)

    return ipaddress


def nics_are_equal(db_nic, gnt_nic):
    for field in NIC_FIELDS:
        if getattr(db_nic, field) != gnt_nic[field]:
            return False
    return True


def process_ganeti_nics(ganeti_nics):
    """Process NIC dict from ganeti"""
    new_nics = []
    for index, gnic in enumerate(ganeti_nics):
        nic_name = gnic.get("name", None)
        if nic_name is not None:
            nic_id = utils.id_from_nic_name(nic_name)
        else:
            # Put as default value the index. If it is an unknown NIC to
            # synnefo it will be created automaticaly.
            nic_id = UNKNOWN_NIC_PREFIX + str(index)
        network_name = gnic.get('network', '')
        network_id = utils.id_from_network_name(network_name)
        network = Network.objects.get(id=network_id)

        # Get the new nic info
        mac = gnic.get('mac')
        ipv4 = gnic.get('ip')
        subnet6 = network.subnet6
        ipv6 = mac2eui64(mac, subnet6.cidr) if subnet6 else None

        firewall = gnic.get('firewall')
        firewall_profile = _reverse_tags.get(firewall)
        if not firewall_profile and network.public:
            firewall_profile = settings.DEFAULT_FIREWALL_PROFILE

        nic_info = {
            'index': index,
            'network': network,
            'mac': mac,
            'ipv4_address': ipv4,
            'ipv6_address': ipv6,
            'firewall_profile': firewall_profile,
            'state': 'ACTIVE'}

        new_nics.append((nic_id, nic_info))
    return dict(new_nics)


def remove_nic_ips(nic, version=None):
    """Remove IP addresses associated with a NetworkInterface.

    Remove all IP addresses that are associated with the NetworkInterface
    object, by returning them to the pool and deleting the IPAddress object. If
    the IP is a floating IP, then it is just disassociated from the NIC.
    If version is specified, then only IP addressses of that version will be
    removed.

    """
    for ip in nic.ips.all():
        if version and ip.ipversion != version:
            continue

        # Update the DB table holding the logging of all IP addresses
        terminate_active_ipaddress_log(nic, ip)

        if ip.floating_ip:
            ip.nic = None
            ip.save()
        else:
            # Release the IPv4 address
            ip.release_address()
            ip.delete()


def terminate_active_ipaddress_log(nic, ip):
    """Update DB logging entry for this IP address."""
    if not ip.network.public or nic.machine is None:
        return
    try:
        ip_log, created = \
            IPAddressLog.objects.get_or_create(server_id=nic.machine_id,
                                               network_id=ip.network_id,
                                               address=ip.address,
                                               active=True)
    except IPAddressLog.MultipleObjectsReturned:
        logmsg = ("Multiple active log entries for IP %s, Network %s,"
                  "Server %s. Cannot proceed!"
                  % (ip.address, ip.network, nic.machine))
        log.error(logmsg)
        raise

    if created:
        logmsg = ("No log entry for IP %s, Network %s, Server %s. Created new"
                  " but with wrong creation timestamp."
                  % (ip.address, ip.network, nic.machine))
        log.error(logmsg)
    ip_log.released_at = datetime.now()
    ip_log.active = False
    ip_log.save()


@transaction.commit_on_success
def process_network_status(back_network, etime, jobid, opcode, status, logmsg):
    if status not in [x[0] for x in BACKEND_STATUSES]:
        raise Network.InvalidBackendMsgError(opcode, status)

    back_network.backendjobid = jobid
    back_network.backendjobstatus = status
    back_network.backendopcode = opcode
    back_network.backendlogmsg = logmsg

    # Note: Network is already locked!
    network = back_network.network

    # Notifications of success change the operating state
    state_for_success = BackendNetwork.OPER_STATE_FROM_OPCODE.get(opcode, None)
    if status == rapi.JOB_STATUS_SUCCESS and state_for_success is not None:
        back_network.operstate = state_for_success

    if (status in (rapi.JOB_STATUS_CANCELED, rapi.JOB_STATUS_ERROR)
       and opcode == 'OP_NETWORK_ADD'):
        back_network.operstate = 'ERROR'
        back_network.backendtime = etime

    if opcode == 'OP_NETWORK_REMOVE':
        network_is_deleted = (status == rapi.JOB_STATUS_SUCCESS)
        if network_is_deleted or (status == rapi.JOB_STATUS_ERROR and not
                                  network_exists_in_backend(back_network)):
            back_network.operstate = state_for_success
            back_network.deleted = True
            back_network.backendtime = etime

    if status == rapi.JOB_STATUS_SUCCESS:
        back_network.backendtime = etime
    back_network.save()
    # Also you must update the state of the Network!!
    update_network_state(network)


def update_network_state(network):
    """Update the state of a Network based on BackendNetwork states.

    Update the state of a Network based on the operstate of the networks in the
    backends that network exists.

    The state of the network is:
    * ACTIVE: If it is 'ACTIVE' in at least one backend.
    * DELETED: If it is is 'DELETED' in all backends that have been created.

    This function also releases the resources (MAC prefix or Bridge) and the
    quotas for the network.

    """
    if network.deleted:
        # Network has already been deleted. Just assert that state is also
        # DELETED
        if not network.state == "DELETED":
            network.state = "DELETED"
            network.save()
        return

    backend_states = [s.operstate for s in network.backend_networks.all()]
    if not backend_states and network.action != "DESTROY":
        if network.state != "ACTIVE":
            network.state = "ACTIVE"
            network.save()
            return

    # Network is deleted when all BackendNetworks go to "DELETED" operstate
    deleted = reduce(lambda x, y: x == y and "DELETED", backend_states,
                     "DELETED")

    # Release the resources on the deletion of the Network
    if deleted:
        if network.ips.filter(deleted=False, floating_ip=True).exists():
            msg = "Cannot delete network %s! Floating IPs still in use!"
            log.error(msg % network)
            raise Exception(msg % network)
        log.info("Network %r deleted. Releasing link %r mac_prefix %r",
                 network.id, network.mac_prefix, network.link)
        network.deleted = True
        network.state = "DELETED"
        if network.mac_prefix:
            if network.FLAVORS[network.flavor]["mac_prefix"] == "pool":
                release_resource(res_type="mac_prefix",
                                 value=network.mac_prefix)
        if network.link:
            if network.FLAVORS[network.flavor]["link"] == "pool":
                release_resource(res_type="bridge", value=network.link)

        # Set all subnets as deleted
        network.subnets.update(deleted=True)
        # And delete the IP pools
        for subnet in network.subnets.all():
            if subnet.ipversion == 4:
                subnet.ip_pools.all().delete()
        # And all the backend networks since there are useless
        network.backend_networks.all().delete()

        # Issue commission
        if network.userid:
            quotas.issue_and_accept_commission(network, delete=True)
            # the above has already saved the object and committed;
            # a second save would override others' changes, since the
            # object is now unlocked
            return
        elif not network.public:
            log.warning("Network %s does not have an owner!", network.id)
    network.save()


@transaction.commit_on_success
def process_network_modify(back_network, etime, jobid, opcode, status,
                           job_fields):
    assert (opcode == "OP_NETWORK_SET_PARAMS")
    if status not in [x[0] for x in BACKEND_STATUSES]:
        raise Network.InvalidBackendMsgError(opcode, status)

    back_network.backendjobid = jobid
    back_network.backendjobstatus = status
    back_network.opcode = opcode

    add_reserved_ips = job_fields.get("add_reserved_ips")
    if add_reserved_ips:
        network = back_network.network
        for ip in add_reserved_ips:
            network.reserve_address(ip, external=True)

    if status == rapi.JOB_STATUS_SUCCESS:
        back_network.backendtime = etime
    back_network.save()


@transaction.commit_on_success
def process_create_progress(vm, etime, progress):

    percentage = int(progress)

    # The percentage may exceed 100%, due to the way
    # snf-image:copy-progress tracks bytes read by image handling processes
    percentage = 100 if percentage > 100 else percentage
    if percentage < 0:
        raise ValueError("Percentage cannot be negative")

    # FIXME: log a warning here, see #1033
#   if last_update > percentage:
#       raise ValueError("Build percentage should increase monotonically " \
#                        "(old = %d, new = %d)" % (last_update, percentage))

    # This assumes that no message of type 'ganeti-create-progress' is going to
    # arrive once OP_INSTANCE_CREATE has succeeded for a Ganeti instance and
    # the instance is STARTED.  What if the two messages are processed by two
    # separate dispatcher threads, and the 'ganeti-op-status' message for
    # successful creation gets processed before the 'ganeti-create-progress'
    # message? [vkoukis]
    #
    #if not vm.operstate == 'BUILD':
    #    raise VirtualMachine.IllegalState("VM is not in building state")

    vm.buildpercentage = percentage
    vm.backendtime = etime
    vm.save()


@transaction.commit_on_success
def create_instance_diagnostic(vm, message, source, level="DEBUG", etime=None,
                               details=None):
    """
    Create virtual machine instance diagnostic entry.

    :param vm: VirtualMachine instance to create diagnostic for.
    :param message: Diagnostic message.
    :param source: Diagnostic source identifier (e.g. image-helper).
    :param level: Diagnostic level (`DEBUG`, `INFO`, `WARNING`, `ERROR`).
    :param etime: The time the message occured (if available).
    :param details: Additional details or debug information.
    """
    VirtualMachineDiagnostic.objects.create_for_vm(vm, level, source=source,
                                                   source_date=etime,
                                                   message=message,
                                                   details=details)


def create_instance(vm, nics, flavor, image):
    """`image` is a dictionary which should contain the keys:
            'backend_id', 'format' and 'metadata'

        metadata value should be a dictionary.
    """

    # Handle arguments to CreateInstance() as a dictionary,
    # initialize it based on a deployment-specific value.
    # This enables the administrator to override deployment-specific
    # arguments, such as the disk template to use, name of os provider
    # and hypervisor-specific parameters at will (see Synnefo #785, #835).
    #
    kw = vm.backend.get_create_params()
    kw['mode'] = 'create'
    kw['name'] = vm.backend_vm_id
    # Defined in settings.GANETI_CREATEINSTANCE_KWARGS

    kw['disk_template'] = flavor.disk_template
    kw['disks'] = [{"size": flavor.disk * 1024}]
    provider = flavor.disk_provider
    if provider:
        kw['disks'][0]['provider'] = provider
        kw['disks'][0]['origin'] = flavor.disk_origin

    kw['nics'] = [{"name": nic.backend_uuid,
                   "network": nic.network.backend_id,
                   "ip": nic.ipv4_address}
                  for nic in nics]

    backend = vm.backend
    depend_jobs = []
    for nic in nics:
        bnet, job_ids = ensure_network_is_active(backend, nic.network_id)
        depend_jobs.extend(job_ids)

    kw["depends"] = create_job_dependencies(depend_jobs)

    # Defined in settings.GANETI_CREATEINSTANCE_KWARGS
    # kw['os'] = settings.GANETI_OS_PROVIDER
    kw['ip_check'] = False
    kw['name_check'] = False

    # Do not specific a node explicitly, have
    # Ganeti use an iallocator instead
    #kw['pnode'] = rapi.GetNodes()[0]

    kw['dry_run'] = settings.TEST

    kw['beparams'] = {
        'auto_balance': True,
        'vcpus': flavor.cpu,
        'memory': flavor.ram}

    kw['osparams'] = {
        'config_url': vm.config_url,
        # Store image id and format to Ganeti
        'img_id': image['backend_id'],
        'img_format': image['format']}

    # Use opportunistic locking
    kw['opportunistic_locking'] = settings.GANETI_USE_OPPORTUNISTIC_LOCKING

    # Defined in settings.GANETI_CREATEINSTANCE_KWARGS
    # kw['hvparams'] = dict(serial_console=False)

    log.debug("Creating instance %s", utils.hide_pass(kw))
    with pooled_rapi_client(vm) as client:
        return client.CreateInstance(**kw)


def delete_instance(vm):
    with pooled_rapi_client(vm) as client:
        return client.DeleteInstance(vm.backend_vm_id, dry_run=settings.TEST)


def reboot_instance(vm, reboot_type):
    assert reboot_type in ('soft', 'hard')
    kwargs = {"instance": vm.backend_vm_id,
              "reboot_type": "hard"}
    # XXX: Currently shutdown_timeout parameter is not supported from the
    # Ganeti RAPI. Until supported, we will fallback for both reboot types
    # to the default shutdown timeout of Ganeti (120s). Note that reboot
    # type of Ganeti job must be always hard. The 'soft' and 'hard' type
    # of OS API is different from the one in Ganeti, and maps to
    # 'shutdown_timeout'.
    #if reboot_type == "hard":
    #    kwargs["shutdown_timeout"] = 0
    if settings.TEST:
        kwargs["dry_run"] = True
    with pooled_rapi_client(vm) as client:
        return client.RebootInstance(**kwargs)


def startup_instance(vm):
    with pooled_rapi_client(vm) as client:
        return client.StartupInstance(vm.backend_vm_id, dry_run=settings.TEST)


def shutdown_instance(vm):
    with pooled_rapi_client(vm) as client:
        return client.ShutdownInstance(vm.backend_vm_id, dry_run=settings.TEST)


def resize_instance(vm, vcpus, memory):
    beparams = {"vcpus": int(vcpus),
                "minmem": int(memory),
                "maxmem": int(memory)}
    with pooled_rapi_client(vm) as client:
        return client.ModifyInstance(vm.backend_vm_id, beparams=beparams)


def get_instance_console(vm):
    # RAPI GetInstanceConsole() returns endpoints to the vnc_bind_address,
    # which is a cluster-wide setting, either 0.0.0.0 or 127.0.0.1, and pretty
    # useless (see #783).
    #
    # Until this is fixed on the Ganeti side, construct a console info reply
    # directly.
    #
    # WARNING: This assumes that VNC runs on port network_port on
    #          the instance's primary node, and is probably
    #          hypervisor-specific.
    #
    log.debug("Getting console for vm %s", vm)

    console = {}
    console['kind'] = 'vnc'

    with pooled_rapi_client(vm) as client:
        i = client.GetInstance(vm.backend_vm_id)

    if vm.backend.hypervisor == "kvm" and i['hvparams']['serial_console']:
        raise Exception("hv parameter serial_console cannot be true")
    console['host'] = i['pnode']
    console['port'] = i['network_port']

    return console


def get_instance_info(vm):
    with pooled_rapi_client(vm) as client:
        return client.GetInstance(vm.backend_vm_id)


def vm_exists_in_backend(vm):
    try:
        get_instance_info(vm)
        return True
    except rapi.GanetiApiError as e:
        if e.code == 404:
            return False
        raise e


def get_network_info(backend_network):
    with pooled_rapi_client(backend_network) as client:
        return client.GetNetwork(backend_network.network.backend_id)


def network_exists_in_backend(backend_network):
    try:
        get_network_info(backend_network)
        return True
    except rapi.GanetiApiError as e:
        if e.code == 404:
            return False


def job_is_still_running(vm):
    with pooled_rapi_client(vm) as c:
        try:
            job_info = c.GetJobStatus(vm.backendjobid)
            return not (job_info["status"] in rapi.JOB_STATUS_FINALIZED)
        except rapi.GanetiApiError:
            return False


def ensure_network_is_active(backend, network_id):
    """Ensure that a network is active in the specified backend

    Check that a network exists and is active in the specified backend. If not
    (re-)create the network. Return the corresponding BackendNetwork object
    and the IDs of the Ganeti job to create the network.

    """
    network = Network.objects.select_for_update().get(id=network_id)
    bnet, created = BackendNetwork.objects.get_or_create(backend=backend,
                                                         network=network)
    job_ids = []
    if bnet.operstate != "ACTIVE":
        job_ids = create_network(network, backend, connect=True)

    return bnet, job_ids


def create_network(network, backend, connect=True):
    """Create a network in a Ganeti backend"""
    log.debug("Creating network %s in backend %s", network, backend)

    job_id = _create_network(network, backend)

    if connect:
        job_ids = connect_network(network, backend, depends=[job_id])
        return job_ids
    else:
        return [job_id]


def _create_network(network, backend):
    """Create a network."""

    tags = network.backend_tag
    subnet = None
    subnet6 = None
    gateway = None
    gateway6 = None
    for _subnet in network.subnets.all():
        if _subnet.dhcp and not "nfdhcpd" in tags:
            tags.append("nfdhcpd")
        if _subnet.ipversion == 4:
            subnet = _subnet.cidr
            gateway = _subnet.gateway
        elif _subnet.ipversion == 6:
            subnet6 = _subnet.cidr
            gateway6 = _subnet.gateway

    if network.public:
        conflicts_check = True
        tags.append('public')
    else:
        conflicts_check = False
        tags.append('private')

    # Use a dummy network subnet for IPv6 only networks. Currently Ganeti does
    # not support IPv6 only networks. To bypass this limitation, we create the
    # network with a dummy network subnet, and make Cyclades connect instances
    # to such networks, with address=None.
    if subnet is None:
        subnet = "10.0.0.0/29"

    try:
        bn = BackendNetwork.objects.get(network=network, backend=backend)
        mac_prefix = bn.mac_prefix
    except BackendNetwork.DoesNotExist:
        raise Exception("BackendNetwork for network '%s' in backend '%s'"
                        " does not exist" % (network.id, backend.id))

    with pooled_rapi_client(backend) as client:
        return client.CreateNetwork(network_name=network.backend_id,
                                    network=subnet,
                                    network6=subnet6,
                                    gateway=gateway,
                                    gateway6=gateway6,
                                    mac_prefix=mac_prefix,
                                    conflicts_check=conflicts_check,
                                    tags=tags)


def connect_network(network, backend, depends=[], group=None):
    """Connect a network to nodegroups."""
    log.debug("Connecting network %s to backend %s", network, backend)

    if network.public:
        conflicts_check = True
    else:
        conflicts_check = False

    depends = create_job_dependencies(depends)
    with pooled_rapi_client(backend) as client:
        groups = [group] if group is not None else client.GetGroups()
        job_ids = []
        for group in groups:
            job_id = client.ConnectNetwork(network.backend_id, group,
                                           network.mode, network.link,
                                           conflicts_check,
                                           depends=depends)
            job_ids.append(job_id)
    return job_ids


def delete_network(network, backend, disconnect=True):
    log.debug("Deleting network %s from backend %s", network, backend)

    depends = []
    if disconnect:
        depends = disconnect_network(network, backend)
    _delete_network(network, backend, depends=depends)


def _delete_network(network, backend, depends=[]):
    depends = create_job_dependencies(depends)
    with pooled_rapi_client(backend) as client:
        return client.DeleteNetwork(network.backend_id, depends)


def disconnect_network(network, backend, group=None):
    log.debug("Disconnecting network %s to backend %s", network, backend)

    with pooled_rapi_client(backend) as client:
        groups = [group] if group is not None else client.GetGroups()
        job_ids = []
        for group in groups:
            job_id = client.DisconnectNetwork(network.backend_id, group)
            job_ids.append(job_id)
    return job_ids


def connect_to_network(vm, nic):
    network = nic.network
    backend = vm.backend
    bnet, depend_jobs = ensure_network_is_active(backend, network.id)

    depends = create_job_dependencies(depend_jobs)

    nic = {'name': nic.backend_uuid,
           'network': network.backend_id,
           'ip': nic.ipv4_address}

    log.debug("Adding NIC %s to VM %s", nic, vm)

    kwargs = {
        "instance": vm.backend_vm_id,
        "nics": [("add", "-1", nic)],
        "depends": depends,
    }
    if vm.backend.use_hotplug():
        kwargs["hotplug"] = True
    if settings.TEST:
        kwargs["dry_run"] = True

    with pooled_rapi_client(vm) as client:
        return client.ModifyInstance(**kwargs)


def disconnect_from_network(vm, nic):
    log.debug("Removing NIC %s of VM %s", nic, vm)

    kwargs = {
        "instance": vm.backend_vm_id,
        "nics": [("remove", nic.backend_uuid, {})],
    }
    if vm.backend.use_hotplug():
        kwargs["hotplug"] = True
    if settings.TEST:
        kwargs["dry_run"] = True

    with pooled_rapi_client(vm) as client:
        jobID = client.ModifyInstance(**kwargs)
        firewall_profile = nic.firewall_profile
        if firewall_profile and firewall_profile != "DISABLED":
            tag = _firewall_tags[firewall_profile] % nic.backend_uuid
            client.DeleteInstanceTags(vm.backend_vm_id, [tag],
                                      dry_run=settings.TEST)

        return jobID


def set_firewall_profile(vm, profile, nic):
    uuid = nic.backend_uuid
    try:
        tag = _firewall_tags[profile] % uuid
    except KeyError:
        raise ValueError("Unsopported Firewall Profile: %s" % profile)

    log.debug("Setting tag of VM %s, NIC %s, to %s", vm, nic, profile)

    with pooled_rapi_client(vm) as client:
        # Delete previous firewall tags
        old_tags = client.GetInstanceTags(vm.backend_vm_id)
        delete_tags = [(t % uuid) for t in _firewall_tags.values()
                       if (t % uuid) in old_tags]
        if delete_tags:
            client.DeleteInstanceTags(vm.backend_vm_id, delete_tags,
                                      dry_run=settings.TEST)

        if profile != "DISABLED":
            client.AddInstanceTags(vm.backend_vm_id, [tag],
                                   dry_run=settings.TEST)

        # XXX NOP ModifyInstance call to force process_net_status to run
        # on the dispatcher
        os_name = settings.GANETI_CREATEINSTANCE_KWARGS['os']
        client.ModifyInstance(vm.backend_vm_id,
                              os_name=os_name)
    return None


def get_instances(backend, bulk=True):
    with pooled_rapi_client(backend) as c:
        return c.GetInstances(bulk=bulk)


def get_nodes(backend, bulk=True):
    with pooled_rapi_client(backend) as c:
        return c.GetNodes(bulk=bulk)


def get_jobs(backend, bulk=True):
    with pooled_rapi_client(backend) as c:
        return c.GetJobs(bulk=bulk)


def get_physical_resources(backend):
    """ Get the physical resources of a backend.

    Get the resources of a backend as reported by the backend (not the db).

    """
    nodes = get_nodes(backend, bulk=True)
    attr = ['mfree', 'mtotal', 'dfree', 'dtotal', 'pinst_cnt', 'ctotal']
    res = {}
    for a in attr:
        res[a] = 0
    for n in nodes:
        # Filter out drained, offline and not vm_capable nodes since they will
        # not take part in the vm allocation process
        can_host_vms = n['vm_capable'] and not (n['drained'] or n['offline'])
        if can_host_vms and n['cnodes']:
            for a in attr:
                res[a] += int(n[a] or 0)
    return res


def update_backend_resources(backend, resources=None):
    """ Update the state of the backend resources in db.

    """

    if not resources:
        resources = get_physical_resources(backend)

    backend.mfree = resources['mfree']
    backend.mtotal = resources['mtotal']
    backend.dfree = resources['dfree']
    backend.dtotal = resources['dtotal']
    backend.pinst_cnt = resources['pinst_cnt']
    backend.ctotal = resources['ctotal']
    backend.updated = datetime.now()
    backend.save()


def get_memory_from_instances(backend):
    """ Get the memory that is used from instances.

    Get the used memory of a backend. Note: This is different for
    the real memory used, due to kvm's memory de-duplication.

    """
    with pooled_rapi_client(backend) as client:
        instances = client.GetInstances(bulk=True)
    mem = 0
    for i in instances:
        mem += i['oper_ram']
    return mem


def get_available_disk_templates(backend):
    """Get the list of available disk templates of a Ganeti backend.

    The list contains the disk templates that are enabled in the Ganeti backend
    and also included in ipolicy-disk-templates.

    """
    with pooled_rapi_client(backend) as c:
        info = c.GetInfo()
    ipolicy_disk_templates = info["ipolicy"]["disk-templates"]
    try:
        enabled_disk_templates = info["enabled_disk_templates"]
        return [dp for dp in enabled_disk_templates
                if dp in ipolicy_disk_templates]
    except KeyError:
        # Ganeti < 2.8 does not have 'enabled_disk_templates'
        return ipolicy_disk_templates


def update_backend_disk_templates(backend):
    disk_templates = get_available_disk_templates(backend)
    backend.disk_templates = disk_templates
    backend.save()


##
## Synchronized operations for reconciliation
##


def create_network_synced(network, backend):
    result = _create_network_synced(network, backend)
    if result[0] != rapi.JOB_STATUS_SUCCESS:
        return result
    result = connect_network_synced(network, backend)
    return result


def _create_network_synced(network, backend):
    with pooled_rapi_client(backend) as client:
        job = _create_network(network, backend)
        result = wait_for_job(client, job)
    return result


def connect_network_synced(network, backend):
    with pooled_rapi_client(backend) as client:
        for group in client.GetGroups():
            job = client.ConnectNetwork(network.backend_id, group,
                                        network.mode, network.link)
            result = wait_for_job(client, job)
            if result[0] != rapi.JOB_STATUS_SUCCESS:
                return result

    return result


def wait_for_job(client, jobid):
    result = client.WaitForJobChange(jobid, ['status', 'opresult'], None, None)
    status = result['job_info'][0]
    while status not in rapi.JOB_STATUS_FINALIZED:
        result = client.WaitForJobChange(jobid, ['status', 'opresult'],
                                         [result], None)
        status = result['job_info'][0]

    if status == rapi.JOB_STATUS_SUCCESS:
        return (status, None)
    else:
        error = result['job_info'][1]
        return (status, error)


def create_job_dependencies(job_ids=[], job_states=None):
    """Transform a list of job IDs to Ganeti 'depends' attribute."""
    if job_states is None:
        job_states = list(rapi.JOB_STATUS_FINALIZED)
    assert(type(job_states) == list)
    return [[job_id, job_states] for job_id in job_ids]

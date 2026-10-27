"""OCI Always Free 抢机 —— 公共逻辑。被 oci_retry.py / oci_retry_micro.py 调用。"""
import os
import sys
import time
import datetime
import oci

# 让 Windows 本地跑的时候中文/符号不会因为 GBK 控制台而崩，Actions 上无影响
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EXIT_OK = 0
EXIT_FATAL = 3


def env(key, default=""):
    raw = os.environ.get(key)
    if raw is None:
        return default
    raw = raw.strip()
    return raw if raw else default


COMPARTMENT_ID = env("OCI_TENANCY")
SSH_PUBLIC_KEY = env("OCI_SSH_PUBLIC_KEY", "ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQDF/utBt0XVgT4oPMLoiS9OlL8UHV3qajggvtc+NMDM1PMEeHKPeC5UFhXTAIbt1wCAB5C7jmWT9HYnGy4rLAHGQIAor9weMRsc6elD5nWfL3BXFXpf5xV/xm5P4tifhaeQMbhsS66zjaeVjjrezezJM+zpeg+bwbgmVfOJAfck1TRF+sROZ47fwCfhjggU8jmukcGZzI7cHTA877G3eV6NFrxmilxxjoCQeGQaQU3vQeVOkUvramZjY3QW5DTsLo728Pfj6uY7INxg+AZl9+csrGv6c0JDJo0WdGBicEyMhRoGRv8/qo3eQsZ4Chlbv9qM9vPGegU8B3Vrm8YDPq5f ssh-key-2026-10-27")
VCN_NAME = env("OCI_VCN_NAME", "retry-vcn")
SUBNET_NAME = env("OCI_SUBNET_NAME", "retry-subnet")
VCN_CIDR = env("OCI_VCN_CIDR", "10.0.0.0/16")
SUBNET_CIDR = env("OCI_SUBNET_CIDR", "10.0.0.0/24")
INGRESS_PORTS = [int(p) for p in env("OCI_INGRESS_PORTS", "22,80,443,8880").replace(" ", "").split(",") if p]
RETRY_INTERVAL = int(env("RETRY_INTERVAL", "60"))
UBUNTU_VERSION = env("UBUNTU_VERSION", "24.04")

# 这些错误重试一万次也不会有结果，直接停链，避免白烧 Actions 时间。
FATAL_CODES = (
    "NotAuthorized",
    "NotAuthenticated",
    "InvalidParameter",
    "MissingParameter",
    "LimitExceeded",
    "QuotaExceeded",
    "CannotParseRequest",
    "InternalServerErrorForMigratedResource",
)


def die(message):
    print("")
    print("=" * 70)
    print("FATAL: " + str(message))
    print("=" * 70)
    sys.exit(EXIT_FATAL)


def load_config():
    if not COMPARTMENT_ID:
        die("环境变量 OCI_TENANCY 为空。请到 GitHub 仓库 Secrets 里配置 OCI_TENANCY（租户 OCID）。")
    try:
        cfg = oci.config.from_file()
    except Exception as exc:
        die("读取 ~/.oci/config 失败：" + str(exc))
    print("region  = " + str(cfg.get("region")))
    print("tenancy = " + str(cfg.get("tenancy")))
    print("user    = " + str(cfg.get("user")))
    print("")
    return cfg


def classify(exc):
    if isinstance(exc, oci.exceptions.ServiceError):
        code = str(getattr(exc, "code", "") or "")
        status = int(getattr(exc, "status", 0) or 0)
        message = str(getattr(exc, "message", "") or "").lower()
        if code in FATAL_CODES or status in (401, 403):
            return "fatal"
        if "out of host capacity" in message or "out of capacity" in message:
            return "capacity"
        return "retry"
    return "retry"


def list_ads(cfg):
    ident = oci.identity.IdentityClient(cfg)
    names = [a.name for a in ident.list_availability_domains(COMPARTMENT_ID).data]
    if not names:
        die("取不到 availability domain。检查 OCI_TENANCY / OCI_USER / OCI_REGION 是否属于同一个租户。")
    return names


def find_image(cfg, shape):
    compute = oci.core.ComputeClient(cfg)
    images = compute.list_images(
        COMPARTMENT_ID,
        operating_system="Canonical Ubuntu",
        operating_system_version=UBUNTU_VERSION,
        shape=shape,
        sort_by="TIMECREATED",
        sort_order="DESC",
    ).data
    if not images:
        print("WARN 找不到 Ubuntu " + UBUNTU_VERSION + " 的 " + shape + " 镜像，回退到最新 Canonical Ubuntu")
        images = compute.list_images(
            COMPARTMENT_ID,
            operating_system="Canonical Ubuntu",
            shape=shape,
            sort_by="TIMECREATED",
            sort_order="DESC",
        ).data
    if not images:
        die("该区域没有 " + shape + " 可用的 Canonical Ubuntu 镜像，shape 可能不被支持。")
    image = images[0]
    print("image   = " + str(image.display_name))
    return image.id


def apply_ingress(network, vcn):
    """把需要的 TCP 端口并进默认安全列表；不覆盖无关规则。"""
    lists = network.list_security_lists(COMPARTMENT_ID, vcn_id=vcn.id).data
    if not lists:
        print("WARN 找不到安全列表，跳过端口放行")
        return
    sl = lists[0]
    wanted = set(INGRESS_PORTS)
    kept = []
    for rule in (sl.ingress_security_rules or []):
        protocol = str(getattr(rule, "protocol", "") or "")
        covered = set()
        tcp = getattr(rule, "tcp_options", None)
        rng = getattr(tcp, "destination_port_range", None) if tcp is not None else None
        if protocol == "all":
            covered = set(wanted)
        elif rng is not None:
            lo = int(rng.min)
            hi = int(rng.max)
            if hi - lo < 128:
                covered = set(range(lo, hi + 1))
            else:
                covered = set(wanted)
        elif protocol == "6":
            covered = set(wanted)
        if covered & wanted:
            continue
        kept.append(rule)
    added = []
    for port in sorted(wanted):
        added.append(
            oci.core.models.IngressSecurityRule(
                protocol="6",
                source="0.0.0.0/0",
                tcp_options=oci.core.models.TcpOptions(
                    destination_port_range=oci.core.models.PortRange(min=port, max=port)
                ),
            )
        )
    network.update_security_list(
        sl.id,
        oci.core.models.UpdateSecurityListDetails(
            ingress_security_rules=kept + added,
            egress_security_rules=sl.egress_security_rules or [],
        ),
    )
    print("ingress = tcp/" + ", tcp/".join(str(p) for p in sorted(wanted)) + "  (0.0.0.0/0)")


def ensure_network(cfg):
    network = oci.core.VirtualNetworkClient(cfg)
    vcns = network.list_vcns(COMPARTMENT_ID, display_name=VCN_NAME).data
    if vcns:
        vcn = vcns[0]
        print("VCN     = 复用 " + str(vcn.id))
    else:
        vcn = network.create_vcn(
            oci.core.models.CreateVcnDetails(
                compartment_id=COMPARTMENT_ID,
                display_name=VCN_NAME,
                cidr_block=VCN_CIDR,
            )
        ).data
        print("VCN     = 新建 " + str(vcn.id))
        ig = network.create_internet_gateway(
            oci.core.models.CreateInternetGatewayDetails(
                compartment_id=COMPARTMENT_ID,
                vcn_id=vcn.id,
                display_name=VCN_NAME + "-ig",
                is_enabled=True,
            )
        ).data
        network.update_route_table(
            vcn.default_route_table_id,
            oci.core.models.UpdateRouteTableDetails(
                route_rules=[
                    oci.core.models.RouteRule(
                        destination="0.0.0.0/0",
                        network_entity_id=ig.id,
                    )
                ]
            ),
        )
        print("IGW     = 新建 " + str(ig.id))
    # 关键：不管 VCN 是新建还是复用，这里都执行（原版只在新建分支里设置端口，复用就永远开不出端口）
    apply_ingress(network, vcn)
    subnets = network.list_subnets(COMPARTMENT_ID, vcn_id=vcn.id, display_name=SUBNET_NAME).data
    if subnets:
        subnet = subnets[0]
        print("subnet  = 复用 " + str(subnet.id))
    else:
        subnet = network.create_subnet(
            oci.core.models.CreateSubnetDetails(
                compartment_id=COMPARTMENT_ID,
                vcn_id=vcn.id,
                display_name=SUBNET_NAME,
                cidr_block=SUBNET_CIDR,
                prohibit_public_ip_on_vnic=False,
            )
        ).data
        print("subnet  = 新建 " + str(subnet.id))
    return subnet.id


def launch(cfg, subnet_id, ad, image_id, shape, ocpus, memory, boot_gb, name):
    compute = oci.core.ComputeClient(cfg)
    kwargs = dict(
        compartment_id=COMPARTMENT_ID,
        display_name=name,
        availability_domain=ad,
        shape=shape,
        source_details=oci.core.models.InstanceSourceViaImageDetails(
            image_id=image_id,
            boot_volume_size_in_gbs=boot_gb,
        ),
        create_vnic_details=oci.core.models.CreateVnicDetails(
            subnet_id=subnet_id,
            assign_public_ip=True,
        ),
        metadata={"ssh_authorized_keys": SSH_PUBLIC_KEY},
    )
    if ocpus and memory:
        kwargs["shape_config"] = oci.core.models.LaunchInstanceShapeConfigDetails(
            ocpus=ocpus,
            memory_in_gbs=memory,
        )
    return compute.launch_instance(oci.core.models.LaunchInstanceDetails(**kwargs)).data


def find_public_ip(cfg, instance_id):
    try:
        compute = oci.core.ComputeClient(cfg)
        network = oci.core.VirtualNetworkClient(cfg)
        for att in compute.list_vnic_attachments(COMPARTMENT_ID, instance_id=instance_id).data:
            vnic = network.get_vnic(att.vnic_id).data
            if getattr(vnic, "public_ip", None):
                return vnic.public_ip
    except Exception:
        return None
    return None


def report_success(cfg, inst):
    print("")
    print("=" * 70)
    print("SUCCESS 实例创建成功")
    print("  name  = " + str(inst.display_name))
    print("  id    = " + str(inst.id))
    print("  shape = " + str(inst.shape))
    print("  state = " + str(inst.lifecycle_state))
    print("  AD    = " + str(inst.availability_domain))
    ip = find_public_ip(cfg, inst.id)
    if ip:
        print("  ip    = " + ip)
        print("  ssh   = ssh -i ssh-key-2026-10-27.key ubuntu@" + ip)
    else:
        print("  ip    = 暂时取不到，去 OCI 控制台看（可能需要等几十秒）")
    print("=" * 70)


def retry(shape, specs, boot_gb, instance_name):
    print("shape   = " + shape)
    if specs:
        print("specs   = " + ", ".join(str(o) + "C/" + str(m) + "G" for o, m in specs))
    print("boot    = " + str(boot_gb) + " GB")
    print("ubuntu  = " + UBUNTU_VERSION)
    print("interval= " + str(RETRY_INTERVAL) + "s")
    print("ports   = " + ",".join(str(p) for p in INGRESS_PORTS))
    print("")
    cfg = load_config()
    subnet_id = ensure_network(cfg)
    ads = list_ads(cfg)
    print("ADs     = " + " | ".join(ads))
    if len(ads) == 1:
        print("          （本区只有 1 个 AD，换 AD 这条路走不通，只能靠持续重试）")
    image_id = find_image(cfg, shape)
    print("")
    attempt = 0
    started = time.time()
    while True:
        attempt += 1
        ad = ads[(attempt - 1) % len(ads)]
        if specs:
            ocpus, memory = specs[(attempt - 1) % len(specs)]
            label = str(ocpus) + "C/" + str(memory) + "G"
        else:
            ocpus, memory = None, None
            label = shape
        elapsed = int(time.time() - started)
        stamp = datetime.datetime.now().strftime("%H:%M:%S")
        print("[" + stamp + "] #" + str(attempt) + "  已重试 " + str(elapsed // 60) + " 分钟  " + label)
        try:
            inst = launch(cfg, subnet_id, ad, image_id, shape, ocpus, memory, boot_gb, instance_name)
            report_success(cfg, inst)
            sys.exit(EXIT_OK)
        except oci.exceptions.ServiceError as exc:
            kind = classify(exc)
            code = str(getattr(exc, "code", "") or "")
            msg = str(getattr(exc, "message", "") or "").strip()
            if len(msg) > 150:
                msg = msg[:150] + "..."
            if kind == "fatal":
                die(code + " " + msg + " —— 这个错误重试没有意义，已停止接力。")
            if kind == "capacity":
                print("   ❌ 没容量（Out of host capacity）—— 继续等")
            else:
                print("   ❌ " + code + ": " + msg)
        except Exception as exc:
            print("   ⚠️ " + type(exc).__name__ + ": " + str(exc)[:120])
        time.sleep(RETRY_INTERVAL)

# 🚀 LXC AutoScale

**LXC AutoScale** is a resource management daemon specifically designed for Proxmox environments. It automatically adjusts CPU and memory allocations with no downtime and can clone LXC containers based on real-time usage metrics and predefined thresholds. Can be run locally or remotely to make your containers always optimized for performance, managing spikes in demand, and optionally preserving resources during off-peak hours. This project was created by Fabrizio Salmi and this is just a fork where I added new features based on my own needs within my Proxmox home lab.

- **✅ Works with `Proxmox 8.4.5`** 

**Quick Start**

| Method           | Instructions                                                                                                   |
|------------------|----------------------------------------------------------------------------------------------------------------|
| 🐳    | [Docker](https://github.com/MatrixMagician/proxmox-lxc-autoscale/blob/main/docs/lxc_autoscale/README.md#docker) |
| 🐧    | [no Docker](https://github.com/MatrixMagician/proxmox-lxc-autoscale/blob/main/README.md#quick-start) |

## Features
LXC AutoScale is packed with features that make it an essential tool for managing the auto-scaling of your LXC containers on Proxmox:

### Core Scaling Features
- ⚙️ **Automatic Resource Scaling** - Dynamic CPU and memory adjustment based on real-time usage
- ⚖️ **Automatic Horizontal Scaling** - Clone containers automatically when demand increases
- 📊 **Tier Defined Thresholds** - Customizable scaling thresholds per container or container groups
- 🛡️ **Host Resource Reservation** - Protect host resources from over-allocation
- 🔒 **Ignore Scaling Option** - Exclude specific containers from scaling operations
- 🌱 **Energy Efficiency Mode** - Reduce resource allocation during off-peak hours
- 🚦 **Container Prioritization** - Different scaling behaviors based on container importance

### Advanced Features
- 📦 **Automatic Backups** - Container settings backup before scaling operations
- 🔔 **Multi-Channel Notifications** - Email, Gotify, and push notifications for scaling events
- 📈 **Structured JSON Metrics** - Comprehensive performance and scaling metrics
- 💻 **Hybrid Execution** - Run locally on Proxmox host or remotely via SSH
- 💃 **Easy Auto-Configuration** - Automated configuration generation for all containers
- 🐳 **Docker Support** - Containerized deployment option

### Security Features *(New in v2.0)*
- 🔐 **Enhanced Security** - Input validation, command injection prevention, and secure operations
- 🏗️ **Modular Architecture** - Maintainable, testable, and extensible codebase
- 📊 **Structured Logging** - JSON-formatted logs with performance metrics and error tracking
- 🔄 **Retry Mechanisms** - Automatic retry for transient failures with exponential backoff
- ⚡ **Connection Pooling** - Optimized SSH connection management for remote operations
- 🛡️ **Centralized Error Handling** - Comprehensive error management with graceful degradation
- 📈 **Performance Monitoring** - Real-time performance metrics and utilization tracking
- 🔧 **Configuration Management** - Centralized, validated configuration with type safety
- 🧪 **Testing Framework** - Modular design enables comprehensive unit and integration testing


## Quick Start

Getting started with LXC AutoScale on your Proxmox host is quick and simple:

```bash
curl -sSL https://raw.githubusercontent.com/MatrixMagician/proxmox-lxc-autoscale/main/install.sh | bash
```

> [!TIP]
> Once installed, the service should be up and running. You can verify this by executing:
>
> ```bash
> systemctl status lxc_autoscale.service
> ```

If the conditions set in the configuration are met, you will quickly observe scaling operations in action.

> [!IMPORTANT]
> You need to check your `/lib/systemd/system/lxcfs.service` file for the presence of the `-l` option which makes `loadavg` retrieval working as expected. Here the required configuration:
>
> ```
> [Unit]
> Description=FUSE filesystem for LXC
> ConditionVirtualization=!container
> Before=lxc.service
> Documentation=man:lxcfs(1)
> 
> [Service]
> OOMScoreAdjust=-1000
> ExecStartPre=/bin/mkdir -p /var/lib/lxcfs
> # ExecStart=/usr/bin/lxcfs /var/lib/lxcfs
> ExecStart=/usr/bin/lxcfs /var/lib/lxcfs -l
> KillMode=process
> Restart=on-failure
> ExecStopPost=-/bin/fusermount -u /var/lib/lxcfs
> Delegate=yes
> ExecReload=/bin/kill -USR1 $MAINPID
>
> [Install]
> WantedBy=multi-user.target
> ```
> 
> Just update the `/lib/systemd/system/lxcfs.service` file, execute `systemctl daemon-reload && systemctl restart lxcfs` and when you are ready to apply the fix restart the LXC containers.
> 
> _Tnx to No-Pen9082 to point me out to that. [Here](https://forum.proxmox.com/threads/lxc-containers-shows-hosts-load-average.45724/page-2) the Proxmox forum thread on the topic._

## Configuration

LXC AutoScale is designed to be highly customizable. You can reconfigure the service at any time to better suit your specific needs. For detailed instructions on how to adjust the settings, please refer to the **[official documentation](https://github.com/fabriziosalmi/proxmox-lxc-autoscale/blob/main/docs/lxc_autoscale/README.md)**.

> [!TIP]
> If You need LXC AutoScale configuration for all your LXC containers You can automatically generate it by running this command:
> ```
> curl -sSL https://raw.githubusercontent.com/MatrixMagician/proxmox-lxc-autoscale/main/lxc_autoscale/lxc_autoscale_autoconf.sh | bash
> ```

### Additional resources
LXC AutoScale and LXC AutoScale ML can be used and extended in many ways, here some useful additional resources:

- 🌐 [LXC AutoScale UI - Simple web UI to check scaling actions and logs](https://github.com/fabriziosalmi/proxmox-lxc-autoscale/tree/main/lxc_autoscale/ui)
- 🎛️ [LXC AutoScale - TIER snippets for 40 self-hosted apps](https://github.com/fabriziosalmi/proxmox-lxc-autoscale/blob/main/docs/lxc_autoscale/examples/README.md)


## Contributing

This is an experimental fork of LXC AutoScale and as such contributions are not wanted in this repo.  But if you want to contribute to the original project that this fork is based on, please submit a pull request, report an issue, or suggest a new feature, you can get involved below by:

- [Opening an issue](https://github.com/fabriziosalmi/proxmox-lxc-autoscale/issues/new/choose) to report bugs or request new features.
- Submitting a pull request to the original repository.
- Fork the repository to experiment and develop your custom features.

## Credit
All credit goes to [Fabrizio Salmi](https://github.com/fabriziosalmi) for creating this project.

## ⚠️ Disclaimer
> [!CAUTION]
> The author assumes no responsibility for any damage or issues that may arise from using this tool.

## License

LXC AutoScale is licensed under the MIT License, which means you are free to use, modify, and distribute this software with proper attribution. For more details, please see the [LICENSE](LICENSE) file.

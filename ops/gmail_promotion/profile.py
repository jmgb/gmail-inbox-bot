"""Fixed runtime shape. Only digest, approved config and administrator env vary."""

IMAGE = "ghcr.io/jmgb/gmail-inbox-bot@"


def build_profile(image_digest, config, environment, approval):
    return {
        "name": "gmail-inbox-bot",
        "services": {
            "gmail-inbox-bot": {
                "image": IMAGE + image_digest,
                "container_name": "gmail-inbox-bot",
                "environment": {k: v.replace("$", "$$") for k, v in environment.items()},
                "ports": ["127.0.0.1:8007:8000"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(config),
                        "target": "/app/config",
                        "read_only": True,
                    },
                    {
                        "type": "bind",
                        "source": "/home/ubuntu/services/gmail-inbox-bot/logs",
                        "target": "/app/logs",
                    },
                    {
                        "type": "bind",
                        "source": "/home/ubuntu/autofix-control-plane/logs/telegram",
                        "target": "/var/log/telegram-activity",
                    },
                ],
                "restart": "unless-stopped",
                "networks": ["pymechat-net", "docker-proxy"],
                "labels": {"security.gmail.approval": approval},
                "deploy": {
                    "resources": {"limits": {"memory": "256M"}, "reservations": {"memory": "128M"}}
                },
            }
        },
        "networks": {
            "pymechat-net": {"external": True, "name": "pymechat-net"},
            "docker-proxy": {"external": True, "name": "gmail-inbox-bot_docker-proxy"},
        },
    }

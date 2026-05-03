# --- 1. IAM ROLE FOR SSM (No SSH needed) ---
resource "aws_iam_role" "observability_role" {
  name = "k3s_observability_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17",
    Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

# Attach the SSM policy so you can connect from the AWS Console
resource "aws_iam_role_policy_attachment" "observability_ssm" {
  role       = aws_iam_role.observability_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

# Attach EC2 Read Only policy so Prometheus can discover EC2 targets for scraping
resource "aws_iam_role_policy_attachment" "observability_ec2_readonly" {
  role       = aws_iam_role.observability_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ReadOnlyAccess"
}

resource "aws_iam_instance_profile" "observability_profile" {
  name = "k3s_observability_profile"
  role = aws_iam_role.observability_role.name
}


# --- 2. SECURITY GROUP ---
resource "aws_security_group" "observability_sg" {
  name        = "observability-k3s-sg"
  description = "Security group for the K3s Monitoring Server"

  # Prometheus web UI
  ingress {
    from_port   = 9090
    to_port     = 9090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Replace with your home IP later!
  }

  # Prometheus web UI external access
  ingress {
    from_port   = 30090 
    to_port     = 30090
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Replace with your home IP later!
  }

  # Kibana web UI external access
  ingress {
    from_port   = 30056 
    to_port     = 30056
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Replace with your home IP later!
  }

  # Allow Bot instances to send logs to Elasticsearch
  ingress {
    from_port   = 30092
    to_port     = 30092
    protocol    = "tcp"
    security_groups = [aws_security_group.ec2_sg.id] # Only allow from EC2 instances with the right SG
  }

  # Allow ArgoCD external Web UI access (NodePort 30443)
  ingress {
    from_port   = 30443 
    to_port     = 30443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Replace with your home IP later!
  }


  ingress {
    description = "Kubernetes API Access for Terraform"
    from_port   = 6443
    to_port     = 6443
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # For production, restrict this to YOUR home IP address
  }

  ingress {
    description = "ArgoCD Web UI Tunnel"
    from_port   = 8888
    to_port     = 8888
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] # Restrict to your IP if you want to be strictly secure
  }

  # Allow Grafana UI (Replace 0.0.0.0/0 with your home IP later!)
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] 
  }

  # Allow Grafana UI external access
  ingress {
    from_port   = 30000
    to_port     = 30000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] 
  }



  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

resource "aws_security_group_rule" "observability_logstash_from_ec2" {
  type                     = "ingress"
  from_port                = 5044
  to_port                  = 5044
  protocol                 = "tcp"
  security_group_id        = aws_security_group.observability_sg.id
  source_security_group_id = aws_security_group.ec2_sg.id
}

resource "aws_security_group_rule" "observability_prometheus_from_ec2" {
  type                     = "ingress"
  from_port                = 9090
  to_port                  = 9090
  protocol                 = "tcp"
  security_group_id        = aws_security_group.observability_sg.id
  source_security_group_id = aws_security_group.ec2_sg.id
}

# --- 3. THE K3S SERVER ---
resource "aws_instance" "observability_server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "m7i-flex.large"
  iam_instance_profile   = aws_iam_instance_profile.observability_profile.name # Added SSM Profile
  vpc_security_group_ids = [aws_security_group.observability_sg.id]
  
  root_block_device {
    volume_size = 50
    volume_type = "gp3"
  }

  user_data = <<-EOF
              #!/bin/bash
              apt-get update -y
              curl -sfL https://get.k3s.io | sh -

              # Wait for K3s to generate the config file
              while [ ! -f /etc/rancher/k3s/k3s.yaml ]; do sleep 2; done

              mkdir -p /home/ubuntu/.kube
              cp /etc/rancher/k3s/k3s.yaml /home/ubuntu/.kube/config
              chown -R ubuntu:ubuntu /home/ubuntu/.kube
              EOF

  tags = { Name = "Observability-K3s-Server" }
}


# --- 4. OUTPUTS ---
output "observability_public_ip" {
  value = aws_instance.observability_server.public_ip
}

output "observability_private_ip" {
  value = aws_instance.observability_server.private_ip
}
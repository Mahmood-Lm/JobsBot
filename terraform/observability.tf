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

resource "aws_iam_instance_profile" "observability_profile" {
  name = "k3s_observability_profile"
  role = aws_iam_role.observability_role.name
}


# --- 2. SECURITY GROUP ---
resource "aws_security_group" "observability_sg" {
  name        = "observability-k3s-sg"
  description = "Security group for the K3s Monitoring Server"

  # Allow Bot to send logs to Logstash
  # ingress {
  #   from_port       = 5044
  #   to_port         = 5044
  #   protocol        = "tcp"
  #   security_groups = [aws_security_group.ec2_sg.id] 
  # }

  # Allow Bot to send metrics to Prometheus
  # ingress {
  #   from_port       = 9090
  #   to_port         = 9090
  #   protocol        = "tcp"
  #   security_groups = [aws_security_group.ec2_sg.id]
  # }

  # Allow Grafana UI (Replace 0.0.0.0/0 with your home IP later!)
  ingress {
    from_port   = 3000
    to_port     = 3000
    protocol    = "tcp"
    cidr_blocks = ["0.0.0.0/0"] 
  }

  # Allow K3s API access
  ingress {
    from_port   = 6443
    to_port     = 6443
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


# --- 3. THE K3S SERVER ---
resource "aws_instance" "observability_server" {
  ami                    = data.aws_ami.ubuntu.id
  instance_type          = "m7i-flex.large"
  iam_instance_profile   = aws_iam_instance_profile.observability_profile.name # Added SSM Profile
  vpc_security_group_ids = [aws_security_group.observability_sg.id]
  
  root_block_device {
    volume_size = 20
    volume_type = "gp3"
  }

  user_data = <<-EOF
              #!/bin/bash
              apt-get update -y
              curl -sfL https://get.k3s.io | sh -
              
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
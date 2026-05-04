provider "aws" { region = var.aws_region }

# --- 0. NETWORK AUTO-DISCOVERY (Multi-AZ) ---
data "aws_vpc" "default" {
  default = true
}

data "aws_subnet" "zone_a" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.aws_region}a" 
}

data "aws_subnet" "zone_b" {
  vpc_id            = data.aws_vpc.default.id
  availability_zone = "${var.aws_region}b"
}

# --- 1. ECR REPOSITORIES ---
resource "aws_ecr_repository" "bot_repo" {
  name         = "linkedin-bot-v2"
  force_delete = true
}
resource "aws_ecr_repository" "scraper_repo" {
  name         = "linkedin-scraper-v2"
  force_delete = true
}

# --- 2. DYNAMODB (MULTI-TENANT) ---
resource "aws_dynamodb_table" "subscriptions_table" {
  name         = "Subscriptions-V2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "subscription_id"
  attribute {
    name = "subscription_id"
    type = "S"
    }
}

resource "aws_dynamodb_table" "seen_jobs_table" {
  name         = "SeenJobs-V2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_job_id"
  attribute {
    name = "user_job_id"
    type = "S"
    }
}

resource "aws_dynamodb_table" "users_table" {
  name           = "Users-V2"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "chat_id"
  attribute {
    name = "chat_id"
    type = "S"
    }
}

# --- 3. SECURITY GROUPS (The Fix) ---
resource "aws_security_group" "alb_sg" {
  name        = "job-bot-alb-sg"
  description = "Allow inbound web traffic to the ALB from CloudFront/Internet"
  
  ingress {
    from_port   = 80
    to_port     = 80
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

resource "aws_security_group" "ec2_sg" {
  name        = "telegram-bot-v2-sg"
  description = "Allow inbound Port 80 for Webhooks, all outbound" # Incorrect description, but let's not waste time changing it now
  
  ingress {
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.alb_sg.id] # The Magic Lock
  }

  # --- Allow Prometheus to scrape bot metrics ---
  ingress {
    description     = "Traffic from the Observability Server"
    from_port       = 80
    to_port         = 80
    protocol        = "tcp"
    security_groups = [aws_security_group.observability_sg.id] 
  }

  # --- Allow Prometheus to scrape Hardware Metrics ---
  ingress {
    description     = "Node Exporter from Observability Server"
    from_port       = 9100
    to_port         = 9100
    protocol        = "tcp"
    security_groups = [aws_security_group.observability_sg.id] 
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# --- 4. BOT INFRASTRUCTURE (ALB + ASG) ---
resource "aws_iam_role" "ec2_role" {
  name = "telegram_bot_v2_ec2_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "ec2.amazonaws.com" } }]
  })
}

resource "aws_iam_role_policy_attachment" "ec2_lambda" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AWSLambda_FullAccess"
}
resource "aws_iam_role_policy_attachment" "ec2_dynamodb" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}
resource "aws_iam_role_policy_attachment" "ec2_ssm" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}
resource "aws_iam_role_policy_attachment" "ec2_ecr" {
  role       = aws_iam_role.ec2_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

resource "aws_iam_instance_profile" "ec2_profile" {
  name = "telegram_bot_v2_ec2_profile"
  role = aws_iam_role.ec2_role.name
}

data "aws_ami" "ubuntu" {
  most_recent = true
  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }

  owners = ["099720109477"]
}

# The Load Balancer
resource "aws_lb" "bot_alb" {
  name               = "job-bot-alb"
  internal           = false
  load_balancer_type = "application"
  security_groups    = [aws_security_group.alb_sg.id]
  subnets            = [data.aws_subnet.zone_a.id, data.aws_subnet.zone_b.id]
}

resource "aws_lb_target_group" "bot_tg" {
  name     = "job-bot-target-group"
  port     = 80
  protocol = "HTTP"
  vpc_id   = data.aws_vpc.default.id
}

resource "aws_lb_listener" "front_end" {
  load_balancer_arn = aws_lb.bot_alb.arn
  port              = "80"
  protocol          = "HTTP"

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.bot_tg.arn
  }
}

# The Server Blueprint
resource "aws_launch_template" "bot_template" {
  name_prefix   = "job-bot-template"
  image_id      = data.aws_ami.ubuntu.id
  instance_type = "t3.micro"
  
  iam_instance_profile { name = aws_iam_instance_profile.ec2_profile.name }
  vpc_security_group_ids = [aws_security_group.ec2_sg.id]

  user_data = base64encode(<<-EOF
              #!/bin/bash
              apt-get update -y
              apt-get install -y docker.io amazon-ecr-credential-helper
              systemctl start docker
              systemctl enable docker
              mkdir -p /root/.docker
              echo '{"credsStore": "ecr-login"}' > /root/.docker/config.json
              
              aws ecr get-login-password --region ${var.aws_region} | docker login --username AWS --password-stdin ${aws_ecr_repository.bot_repo.repository_url}
              
              # Start the Telegram Bot (Port 80)
              docker run -d --name bot -p 80:80 --restart unless-stopped \
                -e TELEGRAM_TOKEN="${var.telegram_token}" \
                -e GEMINI_API_KEY="${var.gemini_api_key}" \
                -e WEBHOOK_URL="https://${aws_cloudfront_distribution.bot_cdn.domain_name}" \
                -e SUBSCRIPTIONS_TABLE="${aws_dynamodb_table.subscriptions_table.name}" \
                -e USERS_TABLE="${aws_dynamodb_table.users_table.name}" \
                -e AWS_DEFAULT_REGION="${var.aws_region}" \
                -e PYTHONUNBUFFERED=1 \
                ${aws_ecr_repository.bot_repo.repository_url}:latest

              # Start Node Exporter for Hardware Metrics (Port 9100)
              docker run -d --name node-exporter \
                --net="host" \
                --pid="host" \
                -v "/:/host:ro,rslave" \
                --restart unless-stopped \
                quay.io/prometheus/node-exporter:latest \
                --path.rootfs=/host

              # --- INSTALL FILEBEAT ---
              curl -L -O https://artifacts.elastic.co/downloads/beats/filebeat/filebeat-8.12.2-amd64.deb
              dpkg -i filebeat-8.12.2-amd64.deb

              # --- CONFIGURE FILEBEAT ---
              cat << 'FILEBEAT' > /etc/filebeat/filebeat.yml
              filebeat.inputs:
                - type: container
                  paths:
                    - '/var/lib/docker/containers/*/*.log'

              output.logstash:
                # Replace with the private IP of your observability server or a DNS record
                hosts: ["${aws_instance.observability_server.private_ip}:30092"]
              FILEBEAT

              systemctl enable filebeat
              systemctl start filebeat

              EOF
  )
}

# The Scaling Group
resource "aws_autoscaling_group" "bot_asg" {
  name                = "job-bot-asg"
  desired_capacity    = 1
  max_size            = 3
  min_size            = 1
  target_group_arns   = [aws_lb_target_group.bot_tg.arn]
  vpc_zone_identifier = [data.aws_subnet.zone_a.id, data.aws_subnet.zone_b.id]

  launch_template {
    id      = aws_launch_template.bot_template.id
    version = "$Latest"
  }
  
  instance_refresh {
    strategy = "Rolling"
    preferences {
      min_healthy_percentage = 50
    }
  }
}

# Dynamic Scaling Policy based on Traffic
resource "aws_autoscaling_policy" "bot_scaling_policy" {
  name                   = "bot-alb-scaling"
  policy_type            = "TargetTrackingScaling"
  autoscaling_group_name = aws_autoscaling_group.bot_asg.name

  target_tracking_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.bot_alb.arn_suffix}/${aws_lb_target_group.bot_tg.arn_suffix}"
    }
    target_value = 500.0 # Spin up a new bot if we hit 500 requests per minute per bot
  }
}

# --- 5. CLOUDFRONT CDN (Free HTTPS Proxy for Webhooks) ---
resource "aws_cloudfront_distribution" "bot_cdn" {
  enabled             = true
  is_ipv6_enabled     = true
  price_class         = "PriceClass_100" 

  origin {
    domain_name = aws_lb.bot_alb.dns_name
    origin_id   = "BotALB"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" 
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods  = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods   = ["GET", "HEAD"]
    target_origin_id = "BotALB"

    viewer_protocol_policy = "redirect-to-https" 

    min_ttl     = 0
    default_ttl = 0
    max_ttl     = 0

    forwarded_values {
      query_string = true
      cookies { forward = "all" }
    }
  }

  restrictions {
    geo_restriction { restriction_type = "none" }
  }

  viewer_certificate {
    cloudfront_default_certificate = true 
  }
}

# --- 6. LAMBDA SCRAPER INFRASTRUCTURE ---
resource "aws_iam_role" "lambda_role" {
  name = "linkedin_scraper_v2_lambda_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "lambda_dynamodb" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}
resource "aws_iam_role_policy_attachment" "lambda_logs" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}
resource "aws_lambda_function" "bot_lambda" {
  function_name = "linkedin-scraper-function-v2"
  role          = aws_iam_role.lambda_role.arn
  package_type  = "Image"
  image_uri     = "${aws_ecr_repository.scraper_repo.repository_url}:latest"
  memory_size   = 2048
  timeout       = 360 
  environment {
    variables = {
      TELEGRAM_TOKEN = var.telegram_token
      DYNAMODB_TABLE = aws_dynamodb_table.seen_jobs_table.name
      USERS_TABLE    = aws_dynamodb_table.users_table.name
      GEMINI_API_KEY = var.gemini_api_key
    }
  }
}

# --- 7. THE QUEUE (Amazon SQS) ---
resource "aws_sqs_queue" "dlq" {
  name = "linkedin-scraper-dlq"
}
resource "aws_sqs_queue" "scraper_queue" {
  name                       = "linkedin-scraper-queue"
  visibility_timeout_seconds = 400
  message_retention_seconds  = 86400
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3
  })
}
resource "aws_lambda_event_source_mapping" "sqs_to_scraper" {
  event_source_arn = aws_sqs_queue.scraper_queue.arn
  function_name    = aws_lambda_function.bot_lambda.arn
  batch_size       = 1
}
resource "aws_iam_role_policy_attachment" "lambda_sqs_read" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole"
}

# --- 8. THE DISPATCHER LAMBDA ---
resource "aws_iam_role" "dispatcher_role" {
  name = "linkedin_dispatcher_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}
resource "aws_iam_role_policy_attachment" "dispatcher_dynamo" {
  role = aws_iam_role.dispatcher_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonDynamoDBFullAccess"
}
resource "aws_iam_role_policy_attachment" "dispatcher_sqs" {
  role = aws_iam_role.dispatcher_role.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSQSFullAccess"
}
resource "aws_iam_role_policy_attachment" "dispatcher_logs" {
  role = aws_iam_role.dispatcher_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "archive_file" "dummy_dispatcher" {
  type        = "zip"
  output_path = "${path.module}/dummy_dispatcher.zip"
  source {
    content  = "def lambda_handler(event, context): pass"
    filename = "main.py"
  }
}

resource "aws_lambda_function" "dispatcher_lambda" {
  function_name    = "linkedin-dispatcher-function"
  role             = aws_iam_role.dispatcher_role.arn
  handler          = "main.lambda_handler"
  runtime          = "python3.10"
  filename         = data.archive_file.dummy_dispatcher.output_path
  source_code_hash = data.archive_file.dummy_dispatcher.output_base64sha256
  environment {
    variables = {
      SUBSCRIPTIONS_TABLE = aws_dynamodb_table.subscriptions_table.name
      SQS_QUEUE_URL       = aws_sqs_queue.scraper_queue.url
    }
  }
}

resource "aws_cloudwatch_event_rule" "every_minute" {
  name                = "every-minute-dispatcher"
  schedule_expression = "rate(1 minute)"
}
resource "aws_cloudwatch_event_target" "trigger_dispatcher" {
  rule      = aws_cloudwatch_event_rule.every_minute.name
  target_id = "TriggerDispatcher"
  arn       = aws_lambda_function.dispatcher_lambda.arn
}
resource "aws_lambda_permission" "allow_eventbridge_dispatcher" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.dispatcher_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.every_minute.arn
}

output "bot_cloudfront_url" {
  description = "The HTTPS Webhook URL for Telegram"
  value       = "https://${aws_cloudfront_distribution.bot_cdn.domain_name}"
}
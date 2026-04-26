provider "aws" { region = var.aws_region }

# --- 1. ECR REPOSITORIES ---
resource "aws_ecr_repository" "bot_repo" {
  name         = "linkedin-bot-v2"
  force_delete = true
}
resource "aws_ecr_repository" "scraper_repo" {
  name         = "linkedin-scraper-v2"
  force_delete = true
}

# --- 2. DYNAMODB ---
resource "aws_dynamodb_table" "jobs_table" {
  name         = "LinkedInJobs-V2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "job_id"
  attribute { 
    name = "job_id" 
    type = "S"
    }
}

# --- 3. EC2 BOT INFRASTRUCTURE ---
resource "aws_security_group" "ec2_sg" {
  name        = "telegram-bot-v2-sg"
  description = "No inbound traffic allowed, all outbound"
  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

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

resource "aws_instance" "bot_server" {
  ami           = data.aws_ami.ubuntu.id
  instance_type = "t3.micro"
  iam_instance_profile   = aws_iam_instance_profile.ec2_profile.name
  vpc_security_group_ids = [aws_security_group.ec2_sg.id]

  user_data = <<-EOF
              #!/bin/bash
              apt-get update -y
              apt-get install -y docker.io amazon-ecr-credential-helper
              systemctl start docker
              systemctl enable docker
              mkdir -p /root/.docker
              echo '{"credsStore": "ecr-login"}' > /root/.docker/config.json
              EOF
  tags = { Name = "TelegramBot-V2-Server" }
}

# --- 4. LAMBDA SCRAPER INFRASTRUCTURE ---
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
  timeout       = 180
  environment {
    variables = {
      TELEGRAM_TOKEN = var.telegram_token
      CHAT_ID        = var.chat_id
      DYNAMODB_TABLE = aws_dynamodb_table.jobs_table.name
    }
  }
}

# EventBridge Schedule
resource "aws_cloudwatch_event_rule" "schedule" {
  name                = "every-hour-v2"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "trigger_lambda" {
  rule      = aws_cloudwatch_event_rule.schedule.name
  target_id = "TriggerScraperV2"
  arn       = aws_lambda_function.bot_lambda.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromCloudWatch"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.bot_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.schedule.arn
}
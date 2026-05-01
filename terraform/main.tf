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

# --- 2. DYNAMODB (MULTI-TENANT) ---

# Table 1: Stores the User, their URL, and how often to scrape
resource "aws_dynamodb_table" "subscriptions_table" {
  name         = "Subscriptions-V2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "subscription_id" # A unique ID for each link a user tracks

  attribute {
    name = "subscription_id"
    type = "S"
  }
}

# Table 2: Stores the Jobs (Format: "chatId_jobId" so users don't steal each other's alerts)
resource "aws_dynamodb_table" "seen_jobs_table" {
  name         = "SeenJobs-V2"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_job_id"

  attribute {
    name = "user_job_id"
    type = "S"
  }
}

# --- NEW: Users Table for AI CV Profiles ---
resource "aws_dynamodb_table" "users_table" {
  name           = "Users-V2"
  billing_mode   = "PAY_PER_REQUEST"
  hash_key       = "chat_id"

  attribute {
    name = "chat_id"
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
# Give the EC2 Bot Brain permission to read and write to DynamoDB
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
      DYNAMODB_TABLE = aws_dynamodb_table.seen_jobs_table.name
      USERS_TABLE    = aws_dynamodb_table.users_table.name
      GEMINI_API_KEY = var.gemini_api_key
    }
  }
}

# --- 5. THE QUEUE (Amazon SQS) ---
# dead letter queue to hold failed messages for later analysis
resource "aws_sqs_queue" "dlq" {
  name = "linkedin-scraper-dlq"
}

resource "aws_sqs_queue" "scraper_queue" {
  name                       = "linkedin-scraper-queue"
  visibility_timeout_seconds = 200 # Must be equal to or greater than the Scraper Lambda timeout
  message_retention_seconds  = 86400 # Hold failed messages for 1 day
  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.dlq.arn
    maxReceiveCount     = 3 # After 3 fails, send to DLQ
  })
}


# Tell SQS to trigger the Playwright Scraper Lambda
resource "aws_lambda_event_source_mapping" "sqs_to_scraper" {
  event_source_arn = aws_sqs_queue.scraper_queue.arn
  function_name    = aws_lambda_function.bot_lambda.arn
  batch_size       = 1 # Process one URL per Lambda container
}

# Give the Scraper Lambda permission to read from the SQS Queue
resource "aws_iam_role_policy_attachment" "lambda_sqs_read" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaSQSQueueExecutionRole"
}

# --- 6. THE DISPATCHER LAMBDA ---
resource "aws_iam_role" "dispatcher_role" {
  name = "linkedin_dispatcher_role"
  assume_role_policy = jsonencode({
    Version = "2012-10-17", Statement = [{ Action = "sts:AssumeRole", Effect = "Allow", Principal = { Service = "lambda.amazonaws.com" } }]
  })
}

# Dispatcher needs to read DynamoDB, write to SQS, and log to CloudWatch
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

# We will create a dummy zip file just to initialize the Lambda. 
# You will update the code via GitHub Actions later.
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

# The 1-Minute Heartbeat
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
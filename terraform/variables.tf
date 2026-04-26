variable "aws_region" {
  default = "eu-central-1"
}
variable "telegram_token" {
  type      = string
  sensitive = true
}
variable "chat_id" {
  type      = string
  sensitive = true
}
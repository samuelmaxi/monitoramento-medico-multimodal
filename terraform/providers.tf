terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  subscription_id = var.subscription_id
  tenant_id       = "a462459f-425d-457a-9aff-08d017571be3"
}

data "azurerm_client_config" "current" {}

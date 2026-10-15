packer {
  required_plugins {
    amazon = {
      source  = "github.com/hashicorp/amazon"
      version = "~> 1.3"
    }
    ansible = {
      source  = "github.com/hashicorp/ansible"
      version = "~> 1.1"
    }
    # Optional platforms. Only fetched by `packer init`; only built when selected with -only.
    azure = {
      source  = "github.com/hashicorp/azure"
      version = "~> 2.1"
    }
    googlecompute = {
      source  = "github.com/hashicorp/googlecompute"
      version = "~> 1.1"
    }
    vsphere = {
      source  = "github.com/hashicorp/vsphere"
      version = "~> 1.4"
    }
  }
}

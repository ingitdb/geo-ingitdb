# Model licence: CC0-1.0 (repository LICENSE).
# Source data: GeoNames CC-BY-4.0; see DATA-LICENSE.md and snapshot attribution.
# All physical source columns are TEXT: numeric strings and empty values stay exact.
# These are source-qualified projections, not current ISO-membership or settlement assertions.

record "geonames_countries" {
  key = ["iso"]

  field "iso" {
    type = "string"
    required = true
  }

  field "iso3" {
    type = "string"
  }

  field "iso_numeric" {
    type = "string"
  }

  field "fips" {
    type = "string"
  }

  field "country" {
    type = "string"
  }

  field "capital" {
    type = "string"
  }

  field "area_sq_km" {
    type = "string"
  }

  field "population" {
    type = "string"
  }

  field "continent" {
    type = "string"
  }

  field "tld" {
    type = "string"
  }

  field "currency_code" {
    type = "string"
  }

  field "currency_name" {
    type = "string"
  }

  field "phone" {
    type = "string"
  }

  field "postal_code_format" {
    type = "string"
  }

  field "postal_code_regex" {
    type = "string"
  }

  field "languages" {
    type = "string"
  }

  field "geonameid" {
    type = "string"
  }

  field "neighbours" {
    type = "string"
  }

  field "equivalent_fips_code" {
    type = "string"
  }

}

record "geonames_admin1" {
  key = ["code"]

  field "code" {
    type = "string"
    required = true
  }

  field "name" {
    type = "string"
  }

  field "asciiname" {
    type = "string"
  }

  field "geonameid" {
    type = "string"
  }

}

record "geonames_places" {
  key = ["geonameid"]

  field "geonameid" {
    type = "string"
    required = true
  }

  field "name" {
    type = "string"
  }

  field "asciiname" {
    type = "string"
  }

  field "alternatenames" {
    type = "string"
  }

  field "latitude" {
    type = "string"
  }

  field "longitude" {
    type = "string"
  }

  field "feature_class" {
    type = "string"
  }

  field "feature_code" {
    type = "string"
  }

  field "country_code" {
    type = "string"
  }

  field "cc2" {
    type = "string"
  }

  field "admin1_code" {
    type = "string"
  }

  field "admin2_code" {
    type = "string"
  }

  field "admin3_code" {
    type = "string"
  }

  field "admin4_code" {
    type = "string"
  }

  field "population" {
    type = "string"
  }

  field "elevation" {
    type = "string"
  }

  field "dem" {
    type = "string"
  }

  field "timezone" {
    type = "string"
  }

  field "modification_date" {
    type = "string"
  }

}

record "geonames_alternate_names" {
  key = ["alternate_name_id"]

  field "alternate_name_id" {
    type = "string"
    required = true
  }

  field "geonameid" {
    type = "string"
  }

  field "isolanguage" {
    type = "string"
  }

  field "alternate_name" {
    type = "string"
  }

  field "is_preferred_name" {
    type = "string"
  }

  field "is_short_name" {
    type = "string"
  }

  field "is_colloquial" {
    type = "string"
  }

  field "is_historic" {
    type = "string"
  }

  field "from_period" {
    type = "string"
  }

  field "to_period" {
    type = "string"
  }

}

record "geonames_chinook_customer_country" {
  key = ["serving_id"]

  field "serving_id" {
    type = "string"
    required = true
  }

  field "raw_label" {
    type = "string"
    required = true
  }

  field "target_key" {
    type = "string"
    required = true
  }

}

record "geonames_northwind_customer_country" {
  key = ["serving_id"]

  field "serving_id" {
    type = "string"
    required = true
  }

  field "raw_label" {
    type = "string"
    required = true
  }

  field "target_key" {
    type = "string"
    required = true
  }

}

record "geonames_northwind_order_country" {
  key = ["serving_id"]

  field "serving_id" {
    type = "string"
    required = true
  }

  field "raw_label" {
    type = "string"
    required = true
  }

  field "target_key" {
    type = "string"
    required = true
  }

}

record "geonames_pubs_publisher_country" {
  key = ["serving_id"]

  field "serving_id" {
    type = "string"
    required = true
  }

  field "raw_label" {
    type = "string"
    required = true
  }

  field "target_key" {
    type = "string"
    required = true
  }

}

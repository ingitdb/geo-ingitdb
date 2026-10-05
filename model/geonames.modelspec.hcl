# Model licence: CC0-1.0 (repository LICENSE).
# Source data: GeoNames CC-BY-4.0; see DATA-LICENSE.md and snapshot attribution.
# All physical source columns are TEXT: numeric strings and empty values stay exact.
# These are source-qualified projections, not current ISO-membership or settlement assertions.

entity "geonames_countries" {
  key = ["iso"]

  property "iso" {
    type = "string"
    required = true
  }

  property "iso3" {
    type = "string"
  }

  property "iso_numeric" {
    type = "string"
  }

  property "fips" {
    type = "string"
  }

  property "country" {
    type = "string"
  }

  property "capital" {
    type = "string"
  }

  property "area_sq_km" {
    type = "string"
  }

  property "population" {
    type = "string"
  }

  property "continent" {
    type = "string"
  }

  property "tld" {
    type = "string"
  }

  property "currency_code" {
    type = "string"
  }

  property "currency_name" {
    type = "string"
  }

  property "phone" {
    type = "string"
  }

  property "postal_code_format" {
    type = "string"
  }

  property "postal_code_regex" {
    type = "string"
  }

  property "languages" {
    type = "string"
  }

  property "geonameid" {
    type = "string"
  }

  property "neighbours" {
    type = "string"
  }

  property "equivalent_fips_code" {
    type = "string"
  }

}

entity "geonames_admin1" {
  key = ["code"]

  property "code" {
    type = "string"
    required = true
  }

  property "name" {
    type = "string"
  }

  property "asciiname" {
    type = "string"
  }

  property "geonameid" {
    type = "string"
  }

}

entity "geonames_places" {
  key = ["geonameid"]

  property "geonameid" {
    type = "string"
    required = true
  }

  property "name" {
    type = "string"
  }

  property "asciiname" {
    type = "string"
  }

  property "alternatenames" {
    type = "string"
  }

  property "latitude" {
    type = "string"
  }

  property "longitude" {
    type = "string"
  }

  property "feature_class" {
    type = "string"
  }

  property "feature_code" {
    type = "string"
  }

  property "country_code" {
    type = "string"
  }

  property "cc2" {
    type = "string"
  }

  property "admin1_code" {
    type = "string"
  }

  property "admin2_code" {
    type = "string"
  }

  property "admin3_code" {
    type = "string"
  }

  property "admin4_code" {
    type = "string"
  }

  property "population" {
    type = "string"
  }

  property "elevation" {
    type = "string"
  }

  property "dem" {
    type = "string"
  }

  property "timezone" {
    type = "string"
  }

  property "modification_date" {
    type = "string"
  }

}

entity "geonames_alternate_names" {
  key = ["alternate_name_id"]

  property "alternate_name_id" {
    type = "string"
    required = true
  }

  property "geonameid" {
    type = "string"
  }

  property "isolanguage" {
    type = "string"
  }

  property "alternate_name" {
    type = "string"
  }

  property "is_preferred_name" {
    type = "string"
  }

  property "is_short_name" {
    type = "string"
  }

  property "is_colloquial" {
    type = "string"
  }

  property "is_historic" {
    type = "string"
  }

  property "from_period" {
    type = "string"
  }

  property "to_period" {
    type = "string"
  }

}

entity "geonames_chinook_customer_country" {
  key = ["serving_id"]

  property "serving_id" {
    type = "string"
    required = true
  }

  property "raw_label" {
    type = "string"
    required = true
  }

  property "target_key" {
    type = "string"
    required = true
  }

}

entity "geonames_northwind_customer_country" {
  key = ["serving_id"]

  property "serving_id" {
    type = "string"
    required = true
  }

  property "raw_label" {
    type = "string"
    required = true
  }

  property "target_key" {
    type = "string"
    required = true
  }

}

entity "geonames_northwind_order_country" {
  key = ["serving_id"]

  property "serving_id" {
    type = "string"
    required = true
  }

  property "raw_label" {
    type = "string"
    required = true
  }

  property "target_key" {
    type = "string"
    required = true
  }

}

entity "geonames_pubs_publisher_country" {
  key = ["serving_id"]

  property "serving_id" {
    type = "string"
    required = true
  }

  property "raw_label" {
    type = "string"
    required = true
  }

  property "target_key" {
    type = "string"
    required = true
  }

}
